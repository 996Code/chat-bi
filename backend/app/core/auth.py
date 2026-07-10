"""
ChatBI v2 — JWT Authentication + RBAC (T005)

Every API endpoint that needs auth uses Depends(get_current_user).
JWT contains: user_id, email, tenant_id, role (对标 v1 经验教训 #3)

+ Multi-tenant isolation (T006):
  机制: contextvars 存当前 tenant_id + TenantMixin.tenant_filter() 显式过滤。
  所有 tenant-scoped 模型继承 TenantMixin，查询时需 .where(Model.tenant_filter(tid))。
  对标: v1 经验教训 #48 — 多租户隔离应是默认行为

  NOTE: 当前为"半自动"（需调用方显式 filter），非 session event 全局自动注入。
  设计决策: 不引入全局 session event 自动注入。
    理由: (1) 影响 all queries, bug 会导致全系统隔离失效, 风险 > 收益;
          (2) 业务库 SQL 执行(T031)不走 ORM, session event 对它无效;
          (3) 多租户隔离靠 data_source 归属: chat 路径查 DataSource 用 tenant_filter,
              data_source_id 来自本租户 → 业务库连库天然隔离。
    所有 API 端点已显式 .where(tenant_filter()) (data_sources/semantic_models/chat)。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token
from app.db.models import AuditLog, TenantMixin, User

logger = logging.getLogger(__name__)

try:
    from jose import ExpiredSignatureError, JWTError
    from jose.exceptions import JWTClaimsError
except ImportError:
    class ExpiredSignatureError(Exception): pass
    class JWTError(Exception): pass
    class JWTClaimsError(Exception): pass

# ── JWT Auth ───────────────────────────────────────────────────

security_scheme = HTTPBearer(auto_error=False)


class AuthUser:
    """Authenticated user extracted from JWT token."""
    def __init__(self, user_id: str, email: str, tenant_id: str, role: str):
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.role = role


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
) -> AuthUser:
    """FastAPI dependency: extract and validate the current user from JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired, please refresh",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTClaimsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (KeyError, AttributeError, TypeError, ValueError) as e:
        # 对标 M1: JWT payload 结构异常 → 401, 但不吞掉其他编程错误
        # ValueError: jwt.decode 可能对畸形 token (如段数不对) 抛 ValueError
        logger.warning("JWT 解码结构异常: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    # 安全提取 JWT 字段 (对标 fail-closed: 缺字段 → 401 而非 500 KeyError)
    user_id = payload.get("user_id")
    email = payload.get("email")
    tenant_id = payload.get("tenant_id")
    role = payload.get("role")
    if not all([user_id, email, tenant_id, role]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required fields",
        )

    return AuthUser(
        user_id=user_id,
        email=email,
        tenant_id=tenant_id,
        role=role,
    )


# ── RBAC ──────────────────────────────────────────────────────

def require_role(*roles: str):
    """FastAPI dependency factory: require one of the given roles."""
    async def _require_role(current_user: AuthUser = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return current_user
    return _require_role


require_admin = require_role("admin")
require_user = require_role("admin", "user")


# ── Multi-tenant session-level isolation (T006) ────────────────

# Set of model names that are GLOBAL (not tenant-scoped)
GLOBAL_TABLES = {"Tenant"}

# Per-request tenant_id stored in async context
# We use a simple approach: inject tenant_id via a context variable
import contextvars

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant(tenant_id: str) -> None:
    """Set the tenant_id for the current request context."""
    _current_tenant_id.set(tenant_id)


def get_current_tenant() -> str | None:
    """Get the tenant_id for the current request context."""
    return _current_tenant_id.get()


# TenantMixin 现定义于 app.db.models (纯 ORM 基类，避免循环依赖)
# auth.py 通过 re-export 保持向后兼容
__all__ = ["TenantMixin"]


# ── Audit Helpers ──────────────────────────────────────────────

async def write_audit_log(
    db_session,
    tenant_id: str,
    user_id: str | None,
    resource_type: str,
    action: str,
    status: str,
    resource_id: str | None = None,
    detail: dict | None = None,
    sql_text: str | None = None,
    error_message: str | None = None,
    ip_address: str | None = None,
    # DSO-07: 慢查询标记 (SQL 执行耗时 + 是否慢查询)
    duration_ms: int | None = None,
    is_slow: bool = False,
    # DSO-05: 数据源归属 (按源聚合统计用)
    data_source_id: str | None = None,
) -> None:
    """Write an audit log entry in an independent transaction.

    对标 v1 经验教训 #41: 审计日志覆盖成功+失败+拒绝，统一字段
    M7: 审计写入独立事务 — 业务 rollback 不丢失审计记录。
    降级链: 独立 session → 调用方 session → logger.error (至少文件有记录)
    """
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.audit_enabled:
        return

    log_entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        status=status,
        detail=detail,
        sql_text=sql_text,
        error_message=error_message,
        ip_address=ip_address,
        duration_ms=duration_ms,
        is_slow=is_slow,
        data_source_id=data_source_id,
    )

    # M7: 优先独立事务写入 (业务 rollback 不影响审计)
    committed = False
    try:
        from app.db.session import get_engine
        engine = await get_engine()
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        audit_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with audit_factory() as audit_session:
            audit_session.add(log_entry)
            await audit_session.commit()
            committed = True
    except Exception as e:
        # 独立 session 失败 (event loop 差异 / 连接问题) → 降级到调用方 session
        logger.debug("审计独立 session 失败, 降级到调用方 session: %s", e)

    if not committed:
        # 降级: 用调用方 session (业务 rollback 会丢审计, 但至少写入成功)
        try:
            db_session.add(log_entry)
        except Exception as e:
            # 最终降级: logger.error (文件至少有记录)
            logger.error(
                "审计日志写入失败: %s | audit_entry: %s %s → %s",
                e, user_id, action, status,
            )

    logger.debug("Audit: %s %s → %s", user_id, action, status)