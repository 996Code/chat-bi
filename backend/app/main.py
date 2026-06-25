"""
ChatBI v2 — FastAPI Application Entry Point

对标: FastAPI lifespan pattern + v1 main.py
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings, validate_settings_on_startup
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks.

    对标: 连接池/客户端单例在启动时预热，关闭时优雅释放。
    Redis/Milvus 连接失败不阻塞启动（降级模式，对标 v1 fail-closed）。
    """
    # Startup
    setup_logging()
    validate_settings_on_startup()

    # 预热连接（best-effort，失败降级，不阻塞启动）
    # Redis: 语义缓存(T023)/限流用；连不上 → 降级跳过缓存
    from app.core.redis_client import get_redis, close_redis
    try:
        await get_redis()
    except Exception:
        pass  # get_redis 内部已处理降级

    # Milvus: 向量检索(T019+); 启动时探测连通性，连不上不阻塞
    # (本地 infra 可能未起；真正用时再连，降级为检索无结果)
    from app.core.milvus_client import get_milvus_client, close_milvus
    try:
        get_milvus_client()
    except Exception:
        pass

    # Embedder: 本地 BGE 模型(T019); 启动时预热加载
    # 加载失败不阻塞启动 (RAG 降级, 对标 Milvus/Redis fail-closed)
    from app.services.embedder import get_embedder
    try:
        embedder = get_embedder()
        import asyncio
        # 触发实际模型加载 (embed 空列表不加载, 用单条探测)
        await embedder.embed(["启动预热"])
    except Exception as e:
        import logging
        logging.getLogger("app.main").warning(
            "Embedder 加载失败, RAG 将降级: %s", e
        )

    # 调度器: 定时任务 (DSO-02 健康检查 / DSO-04 元数据刷新 / PERF-03 任务清理)
    # 启动失败降级 (fail-closed, 不阻塞应用)
    from app.core.scheduler import start_scheduler, shutdown_scheduler
    try:
        await start_scheduler()
    except Exception as e:
        import logging
        logging.getLogger("app.main").warning("调度器启动失败, 定时任务不可用: %s", e)

    yield

    # Shutdown — 优雅释放连接池
    try:
        shutdown_scheduler()
    except Exception:
        pass
    try:
        await close_redis()
    except Exception:
        pass
    try:
        close_milvus()
    except Exception:
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 限流 (slowapi, 对标 config rate_limit_*)
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.core.rate_limit import get_limiter
    limiter = get_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.app_version}

    # API router
    from app.api import router as api_router

    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
