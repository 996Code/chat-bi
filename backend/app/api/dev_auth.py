"""
开发模式 token 端点 (仅 DEBUG=True 时可用)

用途: 没有登录页时, 前端开发阶段用这个拿 token 调 API。
生产环境 (DEBUG=False) 自动返回 404, 不暴露。

正式登录/注册是 Phase 4+ 的任务。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import create_access_token

router = APIRouter(prefix="/dev", tags=["dev"])


class DevTokenRequest(BaseModel):
    """开发登录: 自填 tenant_id 和 role (便于测试不同角色)。"""
    tenant_id: str = "default_tenant"
    user_id: str = "dev_user"
    email: str = "dev@chatbi.local"
    role: str = "admin"  # admin | user | read_only


@router.post("/token")
async def get_dev_token(body: DevTokenRequest):
    """获取开发 token。仅 DEBUG 模式可用 (生产自动 404)。"""
    settings = get_settings()
    if not settings.debug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    token = create_access_token({
        "user_id": body.user_id,
        "email": body.email,
        "tenant_id": body.tenant_id,
        "role": body.role,
    })
    return {"access_token": token, "token_type": "bearer"}
