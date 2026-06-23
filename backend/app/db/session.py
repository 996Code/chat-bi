"""
ChatBI v2 — Database Session Management

Engine creation is lazy to support SQLite in tests (no pool_size for SQLite).

对标: v1 db/session.py — SQLAlchemy async session factory
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""
    pass


_engine = None
_async_session_factory = None
_engine_lock = asyncio.Lock()


def _get_engine_kwargs(database_url: str, debug: bool) -> dict:
    """Build engine kwargs based on database type.

    SQLite (used in tests) doesn't support pool_size/max_overflow.
    PostgreSQL supports the full set of pooling options.
    """
    kwargs = {"echo": debug}

    is_sqlite = "sqlite" in database_url
    is_postgresql = "postgresql" in database_url or "asyncpg" in database_url

    if is_sqlite:
        # SQLite: use StaticPool, no pool_size kwargs
        kwargs["connect_args"] = {"check_same_thread": False}
    elif is_postgresql:
        kwargs.update({
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
        })
    # MySQL: also supports pool_size
    elif "mysql" in database_url or "aiomysql" in database_url:
        kwargs.update({
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
        })

    return kwargs


async def get_engine():
    """Get or create the async SQLAlchemy engine (lazy init, thread-safe)."""
    global _engine
    if _engine is not None:
        return _engine

    async with _engine_lock:
        if _engine is not None:  # Double-check after acquiring lock
            return _engine
        from app.core.config import get_settings
        settings = get_settings()
        kwargs = _get_engine_kwargs(settings.database_url, settings.debug)
        _engine = create_async_engine(settings.database_url, **kwargs)
        return _engine


def get_async_session_factory():
    """Get or create the async session factory (lazy init)."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_engine_sync(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


def get_engine_sync():
    """Synchronous engine access for cases where async is not available."""
    global _engine
    if _engine is not None:
        return _engine
    from app.core.config import get_settings
    settings = get_settings()
    kwargs = _get_engine_kwargs(settings.database_url, settings.debug)
    _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async database session."""
    await get_engine()  # Ensure engine is initialized (async-safe)
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
