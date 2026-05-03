"""Test configuration: force SQLite in-memory for tests."""
import os
import sys
from pathlib import Path

# Force SQLite BEFORE any app imports
BASE_DIR = Path(__file__).resolve().parent.parent
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{BASE_DIR / 'chatbi_test.db'}"
os.environ["API_PREFIX"] = "/api/v1"  # keep tests on old prefix

import pytest
import pytest_asyncio
from app.db.base import Base
from app.db.session import engine, async_session_factory


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create tables before each test, drop after."""
    import asyncio

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    yield
    asyncio.get_event_loop().run_until_complete(_teardown())
