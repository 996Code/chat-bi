"""Test configuration: force SQLite in-memory for tests."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Force SQLite BEFORE any app imports
_db_file = os.path.join(tempfile.gettempdir(), f"chatbi_test_{os.getpid()}.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file}"
os.environ["API_PREFIX"] = "/api/v1"  # keep tests on old prefix
os.environ["REDIS_URL"] = ""  # disable Redis in tests — use in-memory fallback

import pytest
import pytest_asyncio
from app.db.base import Base
from app.db.session import engine, async_session_factory


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create tables before each test, drop after. Reset global state."""
    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.run(_setup())
    yield
    # Reset rate limiter state between tests
    from app.core.rate_limiter import _rate_limits
    _rate_limits.clear()
    # Clear dependency overrides (in case test crashed before cleanup)
    from app.main import app
    app.dependency_overrides.clear()
    asyncio.run(_teardown())
