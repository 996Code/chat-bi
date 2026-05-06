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
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["DATA_SOURCE_ENCRYPTION_KEY"] = "test-encrypt-key-32bytes!!"

import pytest
import pytest_asyncio
from app.db.base import Base
from app.db.session import engine, async_session_factory


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create tables before each test, drop after. Reset global state."""
    # Reset rate limiter and login lock state BEFORE each test
    from app.core.rate_limiter import _rate_limits
    _rate_limits.clear()
    from app.services.login_lock_service import _fallback
    _fallback.clear()

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.run(_setup())
    yield
    # Reset rate limiter state between tests
    _rate_limits.clear()
    _fallback.clear()
    # Clear dependency overrides (in case test crashed before cleanup)
    from app.main import app
    app.dependency_overrides.clear()
    asyncio.run(_teardown())


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for API tests."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_header(client):
    """Register a test user and return auth headers."""
    BASE = "/api/v1"
    await client.post(f"{BASE}/auth/register", json={
        "email": "admin@test.com",
        "password": "Test1234!",
    })
    resp = await client.post(f"{BASE}/auth/login", json={
        "email": "admin@test.com",
        "password": "Test1234!",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
