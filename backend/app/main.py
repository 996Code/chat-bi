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
from app.api.conversation import router as conversation_router
from app.api.data_model import router as data_model_router
from app.api.analytics import router as analytics_router
from app.services.connection_pool import pool_manager
from app.core.redis_client import close_redis
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
    # Shutdown: close all connection pools and Redis
    await pool_manager.close_all()
    await close_redis()
    logger.info("All connection pools and Redis disposed")


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

    # Security headers
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "default-src 'self'"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting
    from starlette.middleware.base import BaseHTTPMiddleware
    app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)

    # Include routers
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(datasource_router, prefix=settings.api_prefix)
    app.include_router(query_router, prefix=settings.api_prefix)
    app.include_router(saved_query_router, prefix=settings.api_prefix)
    app.include_router(export_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    app.include_router(feedback_router, prefix=settings.api_prefix)
    app.include_router(conversation_router, prefix=settings.api_prefix)
    app.include_router(data_model_router, prefix=settings.api_prefix)
    app.include_router(analytics_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
