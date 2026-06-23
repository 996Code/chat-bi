"""
ChatBI v2 — API Router (子 router 聚合)

各业务模块在 api/ 下定义自己的 APIRouter, 这里统一聚合挂载到 api_prefix。
main.py: app.include_router(api_router, prefix=settings.api_prefix)
"""
from fastapi import APIRouter

from app.api.data_sources import router as data_sources_router
from app.api.dev_auth import router as dev_auth_router
from app.api.semantic_models import router as semantic_models_router

router = APIRouter()


@router.get("/ping")
async def ping():
    return {"message": "pong"}


# 业务子路由
router.include_router(data_sources_router)
router.include_router(semantic_models_router)
router.include_router(dev_auth_router)
