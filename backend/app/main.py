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
    """Application lifespan — startup and shutdown hooks."""
    # Startup
    setup_logging()
    validate_settings_on_startup()

    settings = get_settings()

    # TODO: Phase 1 — Initialize database connection pool, Redis, Milvus
    # TODO: Phase 1 — Initialize Checkpointer (PostgreSQL)

    yield

    # Shutdown
    # TODO: Phase 1 — Close connection pools


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

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.app_version}

    # API router
    from app.api import router as api_router

    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
