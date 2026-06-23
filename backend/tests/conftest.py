"""
ChatBI v2 — Test Configuration & Fixtures

对标 v1 conftest.py: SQLite in-memory for tests, isolated from real databases
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force test settings before any imports
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-change-in-production-abcdef123456"
os.environ["FERNET_KEY"] = "test-fernet-key-change-in-production-abcdef123456="
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ENV_FILE"] = ""  # Prevent loading .env from disk

# Clear the lru_cache to ensure environment variables take effect
from app.core.config import get_settings
get_settings.cache_clear()


# ── Database fixtures ──────────────────────────────────────────


@pytest.fixture(scope="session")
def test_engine():
    """Create a shared SQLite engine for the test session."""
    from app.core.config import get_settings

    settings = get_settings()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    return engine


@pytest.fixture(scope="session")
def _create_tables(test_engine):
    """Create all tables once for the test session."""
    from app.db.session import Base
    import asyncio

    async def _create():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    yield
    # Tables are auto-deleted when in-memory DB is closed


@pytest.fixture
async def db_session(test_engine, _create_tables) -> AsyncGenerator[AsyncSession, None]:
    """Provide a fresh database session for each test."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session
        await session.rollback()


# ── App fixtures ───────────────────────────────────────────────

@pytest.fixture
def app():
    """Create FastAPI test app."""
    from app.main import create_app
    return create_app()


@pytest.fixture
def settings():
    """Get test settings."""
    from app.core.config import get_settings
    return get_settings()
