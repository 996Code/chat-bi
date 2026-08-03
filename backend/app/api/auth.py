"""
完整认证体系 (AUTH-01~08, V1 生产功能 V2 缺失补齐):
  POST /auth/register  注册 (创建 tenant + user, bcrypt 密码)
  POST /auth/login     登录 (密码校验 + 登录锁定 + email_verified 校验)
  POST /auth/refresh   刷新 access token

对标 V1 经验教训:
  #3  JWT 必须含 user_id/email/tenant_id/role 四字段
  #20 refresh token 必须含完整鉴权字段
  #39 降级要 WARNING (Redis 不可用时内存降级)
  #41 审计三态 (登录成功/失败/拒绝)

登录锁定:
  - 内存计数 (单实例, 多实例需 Redis — 已知限制)
  - 超过 max_login_attempts 锁定 login_lock_minutes 分钟
  - fail-closed: 锁定期内直接拒绝, 不校验密码 (防暴力破解)
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, write_audit_log
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.rate_limit import get_limiter
from app.db.models import Tenant, User
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
_limiter = get_limiter()

# 登录锁定状态 (内存, 单实例; 多实例需 Redis, 已知限制 — 经验教训#39)
# key: email, value: {"fail_count": int, "locked_until": float}
_login_locks: dict[str, dict] = {}

# 简单 email 格式校验 (避免引入 email-validator 依赖)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def _validate_email(email: str) -> str:
    """校验 email 格式 (轻量, 不引入依赖)。"""
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    return email.lower().strip()


def _login_rate():
    """登录限流 (config.rate_limit_login_per_minute)。"""
    return f"{get_settings().rate_limit_login_per_minute}/minute"


# ── 请求 DTO ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # token_type 固定为 "bearer" — 符合 OAuth2 规范, 前端按 bearer 方式携带
    # 前端请求头: Authorization: Bearer <access_token>


# ── 注册 ──────────────────────────────────────────────────────

@_limiter.limit(_login_rate)
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """注册新用户 (AUTH-01)。

    每个注册创建独立 tenant (单租户单用户, 最简单安全)。
    email 唯一约束, 重复注册 → 409。
    """
    email = _validate_email(body.email)
    # 检查 email 唯一
    # 注意: 这里存在竞态条件 (TOCTOU) — 两个并发请求同时注册同一邮箱, 可能都通过检查。
    # 但由于数据库层 email 字段有 UNIQUE 约束, 最终 commit 时第二个会抛 IntegrityError,
    # 由 FastAPI 的数据库异常处理器处理, 返回 409。这里不做手动加锁, 保持简单。
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    # 创建 tenant (每个用户一个)
    tenant = Tenant(name=f"{body.username} 的租户")
    db.add(tenant)
    await db.flush()

    # 创建 user (email_verified=False, DEBUG 模式不强制校验)
    settings = get_settings()
    user = User(
        tenant_id=tenant.id,
        email=email,
        username=body.username,
        hashed_password=hash_password(body.password),
        role="admin",  # 自己注册的租户, 默认 admin
        email_verified=settings.debug,  # DEBUG 模式自动验证, 生产需邮箱验证流程
    )
    db.add(user)
    await db.flush()

    # 审计: 注册成功 — 记录 tenant_id 和 user_id, 用于后续账号追溯
    await write_audit_log(
        db, tenant_id=tenant.id, user_id=user.id,
        resource_type="auth", action="register", status="success",
    )
    await db.commit()

    # JWT token 数据: 包含完整鉴权四字段 (user_id, email, tenant_id, role)
    # 对标经验教训 #3: 任何字段缺失将导致后续鉴权失败
    # 注意: 这里不包含非必要的字段 (如 username), 保持 token 精简
    token_data = {
        "user_id": user.id, "email": user.email,
        "tenant_id": tenant.id, "role": user.role,
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


# ── 登录 ──────────────────────────────────────────────────────

def _check_login_lock(email: str) -> None:
    """检查登录锁定状态 (fail-closed: 锁定期内直接拒绝)。

    内存计数, 单实例; 多实例需 Redis (已知限制, 经验教训#39)。
    """
    settings = get_settings()
    lock = _login_locks.get(email)
    if not lock:
        return
    locked_until = lock.get("locked_until", 0)
    if locked_until > time.time():
        remaining = int((locked_until - time.time()) / 60) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"账号已锁定, 请 {remaining} 分钟后再试 (连续失败 {settings.max_login_attempts} 次)",
        )
    # 锁曾生效但已过期 → "服刑完毕", 重置计数给全新开始
    # 注意: locked_until == 0 表示从未锁定, 不能重置 (否则连续失败计数被清零)
    if locked_until > 0:
        lock["fail_count"] = 0
        lock["locked_until"] = 0


def _record_login_failure(email: str) -> None:
    """记录登录失败 (达到阈值则锁定)。"""
    settings = get_settings()
    lock = _login_locks.setdefault(email, {"fail_count": 0, "locked_until": 0})
    lock["fail_count"] += 1
    if lock["fail_count"] >= settings.max_login_attempts:
        lock["locked_until"] = time.time() + settings.login_lock_minutes * 60
        logger.warning("登录锁定触发: %s (连续失败 %d 次, 锁定 %d 分钟)",
                       email, lock["fail_count"], settings.login_lock_minutes)


def _clear_login_lock(email: str) -> None:
    """登录成功, 清除失败计数。"""
    _login_locks.pop(email, None)


@_limiter.limit(_login_rate)
@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """登录 (AUTH-02)。

    密码校验 + 登录锁定 + email_verified 校验 (生产模式)。
    失败记录审计 + 累计锁定计数 (fail-closed)。
    """
    email = _validate_email(body.email)
    # 1. 锁定检查 (优先, fail-closed)
    _check_login_lock(email)

    # 2. 查用户
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    # 3. 用户不存在 or 密码错 → 统一返回 "邮箱或密码错误" (不泄露用户是否存在)
    # 安全设计: 返回相同错误信息, 防止枚举攻击 (攻击者无法区分"邮箱不存在"和"密码错误")
    if user is None or not verify_password(body.password, user.hashed_password):
        _record_login_failure(email)
        # 审计登录失败: 用户存在时记录 (有合法 tenant_id);
        # 用户不存在时不写审计 (无合法 tenant_id, audit_logs.tenant_id 是 NOT NULL + FK)
        # 这是有意为之: 不存在的用户不留下审计痕迹, 防止攻击者通过审计日志反推用户是否存在
        if user is not None:
            await write_audit_log(
                db, tenant_id=user.tenant_id, user_id=user.id,
                resource_type="auth", action="login", status="fail",
                error_message="邮箱或密码错误",
            )
            await db.commit()
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    # 4. 账号禁用
    if not user.is_active:
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.id,
            resource_type="auth", action="login", status="denied",
            error_message="账号已禁用",
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="账号已禁用")

    # 5. email 未验证 (生产模式校验, DEBUG 跳过)
    settings = get_settings()
    if not settings.debug and not user.email_verified:
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.id,
            resource_type="auth", action="login", status="denied",
            error_message="邮箱未验证",
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="邮箱未验证, 请先完成验证")

    # 6. 成功
    _clear_login_lock(email)
    # 审计登录成功 — 审计三态完整: 登录成功/失败/拒绝, 对标经验教训 #41
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.id,
        resource_type="auth", action="login", status="success",
    )
    await db.commit()

    # JWT token 数据: 刷新时原生支持自动续期, 无需额外逻辑
    # access_token 短期 (默认 30 分钟), refresh_token 长期 (默认 7 天)
    # 前端应在 access_token 过期前调用 /auth/refresh 获取新 token
    token_data = {
        "user_id": user.id, "email": user.email,
        "tenant_id": user.tenant_id, "role": user.role,
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


# ── 刷新 token ────────────────────────────────────────────────

@_limiter.limit(_login_rate)
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    body: RefreshRequest,
):
    """刷新 access token (AUTH-03, 对标经验教训#20)。

    refresh token 必须含完整鉴权字段, 刷新后新 token 同样完整。
    同时校验用户仍存在且活跃, 避免已禁用用户持续刷新 (fail-closed)。

    注意: 这是一个无状态操作 — 不查询 DB 做 session 校验, 仅依赖 JWT 签名。
    如果需要在服务端撤销 refresh token, 需引入黑名单机制 (Redis 或 DB)。
    """
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        # 统一返回 401, 不区分 "token 过期" 和 "token 无效"
        # 防止攻击者通过错误信息推断 token 结构
        raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="token 类型错误, 需要 refresh token")

    # 校验用户仍存在且活跃 (对标 fail-closed: 禁用用户不应持续刷新)
    user_id = payload.get("user_id")
    if user_id:
        from app.db.session import get_db
        async for db in get_db():
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="用户不存在或已禁用")
            break

    # 提取完整鉴权字段 (经验教训#20: 不能丢字段)
    token_data = {
        "user_id": payload["user_id"],
        "email": payload["email"],
        "tenant_id": payload["tenant_id"],
        "role": payload["role"],
    }
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )
