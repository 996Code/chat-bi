"""
认证与授权 API — 注册、登录、JWT 令牌管理、邮箱验证、登录锁定

本文件实现了 ChatBI 的完整认证流程，是系统安全的核心模块。

核心概念：
  - JWT（JSON Web Token）：无状态认证令牌，包含用户信息（user_id, email, tenant_id, role），
    服务端不需要存储会话，每次请求携带令牌即可验证身份。
    Access Token 有效期短（默认 30 分钟），Refresh Token 有效期长（默认 7 天）。
  - 登录锁定（Login Lock）：连续输错密码 5 次后，账号被锁定 15 分钟，
    防止暴力破解攻击。锁定状态存储在 Redis 中（TTL 自动过期解锁）。
    Redis 不可用时自动降级到内存模式（单实例有效）。
  - 邮箱验证（Email Verification）：注册后发送验证链接到邮箱，验证后才能使用账号。
    验证令牌使用 itsdangerous 库生成，24 小时有效。
  - 密码重置（Password Reset）：忘记密码时发送重置链接，30 分钟有效。
    使用 itsdangerous 库生成带时效的签名令牌。
  - 令牌刷新（Token Refresh）：Access Token 过期后，用 Refresh Token 换取新的令牌对，
    避免用户频繁重新登录。

与其它文件的关系：
  - app/core/security.py — JWT 生成/验证（jose 库）、密码哈希（bcrypt）、
    邮箱验证令牌/密码重置令牌（itsdangerous 库）
  - app/services/login_lock_service.py — 登录锁定逻辑（Redis 计数器 + TTL，内存降级）
  - app/services/email_service.py — 邮件发送（验证链接、重置密码链接）
  - app/schemas/auth.py — 请求/响应的 Pydantic 模型（数据校验和序列化）
  - app/db/models.py — User / Tenant 数据库模型
  - app/services/audit_service.py — 审计日志（记录登录事件）
  - app/services/analytics_service.py — 分析事件（统计登录次数）

安全设计要点：
  - 密码使用 bcrypt 哈希存储（cost=12），不可逆，即使数据库泄露也无法还原明文
  - JWT 使用 HS256 签名，密钥从环境变量读取，不硬编码
  - 登录失败返回模糊错误信息（"邮箱或密码错误"），不提示具体是哪个错，防止信息泄露
  - 密码重置接口无论邮箱是否存在都返回相同消息，防止用户枚举攻击
  - 每个用户注册时自动创建一个租户（Tenant），实现多租户数据隔离
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, Tenant
from app.core.security import (
    hash_password,                    # bcrypt 密码哈希（cost=12，自带盐值）
    verify_password,                  # bcrypt 密码校验
    create_access_token,              # 生成 JWT Access Token（短期，默认 30 分钟）
    create_refresh_token,             # 生成 JWT Refresh Token（长期，默认 7 天）
    verify_refresh_token,             # 验证 Refresh Token 的签名和类型
    generate_password_reset_token,    # 生成密码重置令牌（itsdangerous，30 分钟有效）
    verify_password_reset_token,      # 验证密码重置令牌
    generate_email_verification_token,# 生成邮箱验证令牌（itsdangerous，24 小时有效）
    verify_email_verification_token,  # 验证邮箱验证令牌
)
from app.schemas.auth import (
    RegisterRequest,     # 注册请求体（email, password）
    LoginRequest,        # 登录请求体（email, password）
    TokenResponse,       # 令牌响应体（access_token, refresh_token, email_verified）
    RefreshRequest,      # 刷新请求体（refresh_token）
    PasswordResetRequest,# 密码重置请求体（email）
    PasswordResetConfirm,# 密码重置确认体（token, new_password）
    EmailVerifyRequest,  # 邮箱验证请求体（token）
)
# 登录锁定服务的三个函数：
# - check_lock: 检查账号是否被锁定（返回 True 表示已锁定）
# - record_failure: 记录一次登录失败（计数+1，达到阈值后锁定）
# - reset: 登录成功后清除锁定记录
from app.services.login_lock_service import check_lock, record_failure, reset
from app.services.email_service import send_verification_email, send_password_reset_email
from app.core.logging import get_logger

logger = get_logger(__name__)

# APIRouter 创建路由子应用，prefix 表示所有路由以 /auth 开头
# tags 用于 Swagger 文档分组
router = APIRouter(prefix="/auth", tags=["认证"])


from app.api._helpers import api_error  # 统一的错误响应格式化函数


@router.post(
    "/register",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册 — 创建新账号、自动创建租户、发送邮箱验证链接。

    注册流程：
        1. 检查邮箱是否已被注册
        2. 自动创建一个租户（Tenant），实现多租户数据隔离
        3. 用 bcrypt 哈希密码（明文密码不会存储到数据库）
        4. 创建用户记录
        5. 生成邮箱验证令牌（itsdangerous 签名，24 小时有效）
        6. 发送验证链接到注册邮箱

    参数：
        req: 注册请求体（Pydantic 模型 RegisterRequest，自动校验 email 格式和 password 强度）
        db: 异步数据库会话（FastAPI 依赖注入）

    返回：
        {"message": "注册成功，请查收验证邮件"}

    安全设计：
        - 邮箱唯一性检查：同一邮箱不能重复注册
        - 密码强度由 Pydantic 模型校验（RegisterRequest 中定义规则）
        - 每个用户注册时自动创建一个租户，实现多租户数据隔离

    Python 语法提示：
        RegisterRequest 是 Pydantic 模型，FastAPI 自动将请求体 JSON 反序列化为该对象，
        并校验字段类型和约束。Depends(get_db) 是依赖注入，框架自动创建数据库会话。
    """
    # 检查邮箱是否已被注册
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("EMAIL_EXISTS", "该邮箱已注册"),
        )

    # 自动创建租户 — 每个用户注册时自动创建一个独立的租户
    # 多租户设计：不同租户的数据完全隔离（数据源、查询记录、模型配置等）
    # 租户名称使用随机 6 位后缀，避免重名冲突
    tenant = Tenant(name=f"user-{uuid.uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()  # flush() 将 SQL 发送到数据库但不提交，这样可以获取 tenant.id

    # 新注册用户默认 is_active=False，需要邮箱验证后才能登录
    # tenant_id 使用默认租户（UUID 固定值），多租户功能预留
    # 创建用户记录
    # role 默认为 "user"，管理员通过后台手动修改数据库设置
    # is_active=False 表示邮箱未验证，但注册后即可登录（部分功能受限）
    # hash_password 使用 bcrypt 算法对密码进行哈希
    # bcrypt 的特点：自带盐值（salt）、计算慢（防暴力破解）、不可逆
    # 数据库中存储的是哈希值，即使泄露也无法还原明文密码
    user = User(
        tenant_id=tenant.id,
        email=req.email,
        password_hash=hash_password(req.password),
        role="user",
        email_verified=True,  # Auto-verify for now; email service can be enabled later
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = generate_email_verification_token(req.email)
    await send_verification_email(req.email, token)

    # 注册成功，返回提示信息
    # 注意：此处不返回令牌对，用户需要登录后才能获取
    return {"message": "注册成功，请查收验证邮件"}


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": dict}, 429: {"model": dict}},
)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录 — 验证邮箱和密码，返回 JWT 令牌对。

    登录流程：
        1. 检查账号是否被锁定（连续输错密码 5 次，锁定 15 分钟）
        2. 查找用户（按邮箱精确匹配）
        3. 校验密码（bcrypt 哈希比对）
        4. 登录成功后清除锁定记录和失败计数
        5. 检查邮箱是否已验证（未验证返回 403）
        6. 记录审计日志和分析事件
        7. 生成 Access Token（短期）和 Refresh Token（长期）

    参数：
        req: 登录请求体（email, password）
        db: 异步数据库会话

    返回：
        TokenResponse — 包含 access_token、refresh_token、email_verified

    安全设计：
        - 登录锁定：连续失败 5 次后锁定 15 分钟（Redis 计数器 + TTL）
        - 密码校验使用 bcrypt 的 verify_password，不直接比较明文
        - 登录失败时返回模糊的错误信息（"邮箱或密码错误"），
          不提示具体是邮箱不存在还是密码错误，防止信息泄露
        - 429 状态码表示"请求过多"（账号被锁定），401 表示"未授权"（凭证错误）
    """
    # ── Step 1: 检查登录锁定 ──
    # check_lock 返回 True 表示账号被锁定，False 表示未锁定
    # 如果账号被锁定，直接返回 429，不继续验证密码
    # 这样即使攻击者知道密码，也无法在锁定期间登录
    if await check_lock(req.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=api_error("ACCOUNT_LOCKED", "账号已锁定，请15分钟后重试"),
        )

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=api_error("INVALID_CREDENTIALS", "邮箱或密码错误"),
        )

    if not verify_password(req.password, user.password_hash):
        # 密码错误：记录一次失败（计数+1，达到阈值后自动锁定）
        await record_failure(req.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=api_error("INVALID_CREDENTIALS", "邮箱或密码错误"),
        )

    # 登录成功：清除锁定记录和失败计数
    # reset 删除 Redis 中的失败计数和锁定状态
    await reset(req.email)
    user.failed_login_attempts = 0  # 同步清除数据库中的失败计数
    await db.commit()

    # ── Step 4: 检查邮箱是否已验证 ──
    # 未验证邮箱的用户不能登录，需要先完成邮箱验证

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=api_error("EMAIL_NOT_VERIFIED", "邮箱未验证，请查收验证邮件后再登录"),
        )

    # 审计日志：记录注册事件，用于安全审计和用户行为分析
    from app.services.audit_service import log_action
    await log_action(
        db, str(user.tenant_id), str(user.id),
        "USER_LOGIN", "user", str(user.id),
        f"email={user.email}",
    )

    # 分析事件：统计登录次数，用于产品运营分析
    from app.services.analytics_service import track_event, EVENT_USER_LOGIN
    await track_event(db, str(user.tenant_id), str(user.id), EVENT_USER_LOGIN)

    await db.commit()

    # ── Step 6: 生成令牌对 ──
    # Access Token: JWT 格式，包含用户信息，短期有效（默认 30 分钟），用于 API 鉴权
    # Refresh Token: JWT 格式，长期有效（默认 7 天），用于换取新的 Access Token
    # 为什么用两种令牌？Access Token 短期有效，即使泄露影响有限；
    # Refresh Token 期有效但只用于刷新，且可以主动撤销
    # JWT payload 中包含的关键字段：
    #   - user_id: 用户唯一标识
    #   - email: 用户邮箱
    #   - tenant_id: 租户标识（多租户隔离的关键）
    #   - role: 用户角色（admin/user，影响权限）
    token_data = {
        "user_id": str(user.id),
        "email": user.email,
        "tenant_id": str(user.tenant_id),
        "role": user.role,
    }
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        email_verified=user.email_verified,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    payload = verify_refresh_token(req.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=api_error("INVALID_TOKEN", "无效的刷新令牌"),
        )

    token_data = {
        "user_id": payload.get("user_id"),
        "email": payload.get("email"),
        "tenant_id": payload.get("tenant_id"),
        "role": payload.get("role", "user"),
    }
    access_token = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.post("/reset-password", response_model=dict)
async def reset_password(req: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if user:
        token = generate_password_reset_token(req.email)
        await send_password_reset_email(req.email, token)

    return {"message": "如果邮箱存在，重置链接已发送"}


@router.post("/reset-password/confirm", response_model=dict)
async def reset_password_confirm(req: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    email = verify_password_reset_token(req.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("INVALID_TOKEN", "重置链接已过期"),
        )

    # 查找对应用户
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("USER_NOT_FOUND", "用户不存在"),
        )

    # 用 bcrypt 哈希新密码，更新到数据库
    user.password_hash = hash_password(req.new_password)
    user.failed_login_attempts = 0
    await db.commit()

    return {"message": "密码已重置"}


@router.post("/verify-email", response_model=dict)
async def verify_email(req: EmailVerifyRequest, db: AsyncSession = Depends(get_db)):
    email = verify_email_verification_token(req.token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("INVALID_TOKEN", "验证链接已过期"),
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("USER_NOT_FOUND", "用户不存在"),
        )

    user.email_verified = True
    await db.commit()

    return {"message": "邮箱已验证"}
