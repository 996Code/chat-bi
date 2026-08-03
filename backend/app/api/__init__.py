"""
ChatBI v2 — API Router (子 router 聚合)

各业务模块在 api/ 下定义自己的 APIRouter, 这里统一聚合挂载到 api_prefix。
main.py: app.include_router(api_router, prefix=settings.api_prefix)

聚合策略说明:
  - 每个子 router 自带 prefix (如 /auth, /data-sources), 聚合后路径自动拼接
  - 导入顺序: 基础功能 (数据源/语义层) 在前, 业务功能 (看板/对话) 在后
  - /ping 健康检查端点不依赖任何中间件, 用于负载均衡器探活
"""
from fastapi import APIRouter

from app.api.data_sources import router as data_sources_router
from app.api.dev_auth import router as dev_auth_router
from app.api.semantic_models import router as semantic_models_router
from app.api.chat import router as chat_router
from app.api.chat_stream import router as chat_stream_router
from app.api.observability import router as observability_router
from app.api.skills import router as skills_router
from app.api.memory import router as memory_router
from app.api.saved_queries import router as saved_queries_router
from app.api.auth import router as auth_router
from app.api.dashboard import router as dashboard_router
from app.api.graph import router as graph_router

router = APIRouter()


@router.get("/ping")
async def ping():
    """健康检查端点 — 不依赖数据库/中间件, 纯 HTTP 响应。

    用于负载均衡器/容器编排平台 (K8s/ECS) 的存活探活 (liveness probe)。
    """
    return {"message": "pong"}


# 业务子路由 — 按功能域分组聚合, 每个子 router 自带 prefix
# 注意: 挂载顺序影响 OpenAPI 文档中的分组展示, 不影响运行时路由匹配
router.include_router(data_sources_router)
router.include_router(semantic_models_router)
router.include_router(dev_auth_router)
router.include_router(chat_router)
router.include_router(chat_stream_router)
router.include_router(observability_router)
router.include_router(skills_router)
router.include_router(memory_router)
router.include_router(saved_queries_router)
router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(graph_router)
