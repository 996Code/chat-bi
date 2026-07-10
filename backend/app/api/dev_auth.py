"""
开发模式 token 端点 (仅 DEBUG=True 时可用)

用途: 没有登录页时, 前端开发阶段用这个拿 token 调 API。
生产环境 (DEBUG=False) 自动返回 404, 不暴露。

SEC (对标 S3): dev token 必须验证 tenant_id/user_id 在数据库中真实存在,
防止任意 tenant_id 伪造 (即使开发模式也不能绕过租户隔离)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.core.rate_limit import get_limiter
from app.db.models import Tenant, User
from app.db.session import get_db

import logging
logger = logging.getLogger(__name__)

_limiter = get_limiter()

router = APIRouter(prefix="/dev", tags=["dev"])


class DevTokenRequest(BaseModel):
    """开发登录: 自填 tenant_id 和 role (便于测试不同角色)。

    注意: user_id 必须是数据库 users 表里真实存在的用户。
    tenant_id 必须是数据库 tenants 表里真实存在的租户。
    默认用 admin_user / default_tenant (数据库初始化时创建)。
    """
    tenant_id: str = "default_tenant"
    user_id: str = "admin_user"
    email: str = "admin@chatbi.local"
    role: str = "admin"  # admin | user | read_only


def _login_rate():
    """登录限流值 (config.rate_limit_login_per_minute)。"""
    return f"{get_settings().rate_limit_login_per_minute}/minute"


@_limiter.limit(_login_rate)
@router.post("/token")
async def get_dev_token(
    request: Request,
    body: DevTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """获取开发 token。仅 DEBUG 模式可用 (生产自动 404)。

    SEC (对标 S3): 验证 tenant_id 和 user_id 在数据库中真实存在,
    防止任意 tenant_id 伪造 (即使开发模式也不能绕过租户隔离)。
    """
    settings = get_settings()
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # DB 查询包裹 try/except — 防止连接失败泄露 DB 连接细节 (host/port/error stack)
    try:
        # 验证 tenant 存在
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"租户 '{body.tenant_id}' 不存在, 请使用真实 tenant_id",
            )

        # 验证 user 存在且属于该 tenant
        user = (
            await db.execute(
                select(User).where(User.id == body.user_id, User.tenant_id == body.tenant_id)
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"用户 '{body.user_id}' 不存在或不属于租户 '{body.tenant_id}'",
            )
    except HTTPException:
        raise  # 上面主动抛的 400 不吞
    except Exception as e:
        logger.warning("dev_auth DB 查询失败: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂不可用, 请稍后重试",
        )

    # 使用数据库中的真实值 (不信任请求体中的 email/role, 防止提权)
    token_data = {
        "user_id": user.id,
        "email": user.email,
        "tenant_id": user.tenant_id,
        "role": user.role,
    }
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }
