from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limiter import rate_limit_middleware
from app.api.auth import router as auth_router
from app.api.datasource import router as datasource_router
from app.api.query import router as query_router
from app.api.saved_query import router as saved_query_router
from app.api.export import router as export_router
from app.api.audit import router as audit_router
from app.api.feedback import router as feedback_router
from app.services.connection_pool import pool_manager
from app.db.base import Base
from app.db.session import engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (dev convenience — use Alembic for migrations)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")
    yield
    # Shutdown: close all connection pools
    await pool_manager.close_all()
    logger.info("All connection pools disposed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ChatBI",
        description="Natural language to SQL BI platform",
        version="0.1.0",
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

    # Rate limiting (TODO: enable when Redis is available)
    # from starlette.middleware.base import BaseHTTPMiddleware
    # app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)

    # Include routers
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(datasource_router, prefix="/api/v1")
    app.include_router(query_router, prefix="/api/v1")
    app.include_router(saved_query_router, prefix="/api/v1")
    app.include_router(export_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(feedback_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
