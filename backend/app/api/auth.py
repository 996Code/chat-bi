import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import User, Tenant
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    generate_password_reset_token,
    verify_password_reset_token,
    generate_email_verification_token,
    verify_email_verification_token,
)
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    EmailVerifyRequest,
)
from app.services.login_lock_service import check_lock, record_failure, reset
from app.services.email_service import send_verification_email, send_password_reset_email
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post(
    "/register",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("EMAIL_EXISTS", "该邮箱已注册"),
        )

    # Auto-create tenant for each user
    tenant = Tenant(name=f"user-{uuid.uuid4().hex[:6]}")
    db.add(tenant)
    await db.flush()

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

    return {"message": "注册成功，请查收验证邮件"}


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": dict}, 429: {"model": dict}},
)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    if await check_lock(req.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_error("ACCOUNT_LOCKED", "账号已锁定，请15分钟后重试"),
        )

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_error("INVALID_CREDENTIALS", "邮箱或密码错误"),
        )

    if not verify_password(req.password, user.password_hash):
        await record_failure(req.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_error("INVALID_CREDENTIALS", "邮箱或密码错误"),
        )

    await reset(req.email)
    user.failed_login_attempts = 0
    await db.commit()

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_error("EMAIL_NOT_VERIFIED", "邮箱未验证，请查收验证邮件后再登录"),
        )

    # Audit log
    from app.services.audit_service import log_action
    await log_action(
        db, str(user.tenant_id), str(user.id),
        "USER_LOGIN", "user", str(user.id),
        f"email={user.email}",
    )

    # Analytics event
    from app.services.analytics_service import track_event, EVENT_USER_LOGIN
    await track_event(db, str(user.tenant_id), str(user.id), EVENT_USER_LOGIN)

    await db.commit()

    token_data = {
        "user_id": str(user.id),
        "email": user.email,
        "tenant_id": str(user.tenant_id),
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
            detail=_error("INVALID_TOKEN", "无效的刷新令牌"),
        )

    token_data = {
        "user_id": payload.get("user_id"),
        "email": payload.get("email"),
        "tenant_id": payload.get("tenant_id"),
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
            detail=_error("INVALID_TOKEN", "重置链接已过期"),
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("USER_NOT_FOUND", "用户不存在"),
        )

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
            detail=_error("INVALID_TOKEN", "验证链接已过期"),
        )

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("USER_NOT_FOUND", "用户不存在"),
        )

    user.email_verified = True
    await db.commit()

    return {"message": "邮箱已验证"}
