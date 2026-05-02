from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import get_logger
from app.api.auth import router as auth_router
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

    # Include routers
    app.include_router(auth_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
