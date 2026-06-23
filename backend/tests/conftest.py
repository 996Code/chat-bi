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
# Fernet key 必须是 32 字节 url-safe base64（合法格式才能被 Fernet 接受）
os.environ["FERNET_KEY"] = "3OO-go6es96rvMajcdliCWYpXiwvZ_Sckkpe0pQKF40="
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ENV_FILE"] = ""  # Prevent loading .env from disk

# Clear the lru_cache to ensure environment variables take effect
from app.core.config import get_settings
get_settings.cache_clear()


# ── Database fixtures ──────────────────────────────────────────


@pytest.fixture(scope="session")
def test_engine():
    """Create a shared SQLite engine for the test session.

    用 StaticPool + 单连接: in-memory SQLite 默认每个连接独立内存库,
    加 StaticPool 后所有连接共享同一个 → HTTP 请求和 fixture session 同源。
    """
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    """Provide a fresh database session for each test.

    清理策略: 每个测试后 DELETE 所有业务表数据 (而非 rollback),
    因为 HTTP 请求会 commit, rollback 清不掉。
    StaticPool 共享单连接, 所以 HTTP session 和本 session 看同一份数据。
    """
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session
        # 清理: 按依赖反序删 (避免外键约束)
        from sqlalchemy import text
        for table in [
            "feedback", "audit_logs", "saved_queries",
            "conversations", "semantic_models", "data_sources",
            "users", "tenants",
        ]:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()


# ── App fixtures ───────────────────────────────────────────────

@pytest.fixture
def app(test_engine, _create_tables):
    """Create FastAPI test app, wired to the same test engine that has tables.

    关键: app 内部 get_db() 走 session.py 的全局 _engine。
    若不覆盖, app 连的是另一个没建表的引擎 → HTTP 碰 DB 端点会 no such table。
    这里把全局 _engine 指向 test_engine (已建表), 保证 HTTP 和 db_session 同源。
    """
    from app.db import session as session_module
    from app.main import create_app

    # 让 session.py 的全局引擎/工厂指向 test_engine
    session_module._engine = test_engine
    session_module._async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )

    app = create_app()
    yield app

    # 清理全局状态, 避免污染其他测试
    session_module._engine = None
    session_module._async_session_factory = None


@pytest.fixture
def settings():
    """Get test settings."""
    from app.core.config import get_settings
    return get_settings()
