"""Tests for auth module: registration, login, tenant isolation, login lock."""
import asyncio
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app
from app.db.session import async_session_factory, get_db


@pytest_asyncio.fixture(scope="function")
async def db():
    """Fresh DB per test with cleaned tables."""
    async with async_session_factory() as session:
        # Clean all tables (order matters for FKs)
        for table in ["analytics_events", "feedback", "audit_logs", "saved_queries",
                       "metadata_configs", "data_sources", "users", "tenants"]:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass  # Table may not exist in fresh DB
        await session.commit()
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db):
    """Test client with overridden DB dependency."""
    from app.api import auth as auth_module, datasource as ds_module, query as q_module
    from app.api import saved_query as sq_module, export as exp_module, audit as audit_module
    from app.api import feedback as fb_module

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[auth_module.get_db] = override_get_db
    app.dependency_overrides[ds_module.get_db] = override_get_db
    app.dependency_overrides[q_module.get_db] = override_get_db
    app.dependency_overrides[sq_module.get_db] = override_get_db
    app.dependency_overrides[exp_module.get_db] = override_get_db
    app.dependency_overrides[audit_module.get_db] = override_get_db
    app.dependency_overrides[fb_module.get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ─── AUTH-01: Registration ───

@pytest.mark.asyncio
async def test_register_success(client):
    """AUTH-01a: Registration returns 201."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "test@example.com", "password": "Test1234!"
    })
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_register_creates_own_tenant(client):
    """AUTH-01b: Each user gets their own tenant."""
    await client.post("/api/v1/auth/register", json={
        "email": "alice@example.com", "password": "Test1234!"
    })
    await client.post("/api/v1/auth/register", json={
        "email": "bob@example.com", "password": "Test1234!"
    })

    async with async_session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM tenants"))
        count = result.scalar()
        assert count == 2, f"Expected 2 tenants, got {count}"


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    """AUTH-01c: Duplicate email returns 400."""
    await client.post("/api/v1/auth/register", json={
        "email": "dup@example.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/register", json={
        "email": "dup@example.com", "password": "Test1234!"
    })
    assert resp.status_code == 400
    assert "EMAIL_EXISTS" in resp.json()["detail"]["code"]


# ─── AUTH-02: Login ───

@pytest.mark.asyncio
async def test_login_success(client):
    """AUTH-02a: Valid login returns JWT tokens."""
    await client.post("/api/v1/auth/register", json={
        "email": "login@example.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "login@example.com", "password": "Test1234!"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    """AUTH-02b: Wrong password returns 401."""
    await client.post("/api/v1/auth/register", json={
        "email": "wrong@example.com", "password": "Correct123!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "wrong@example.com", "password": "Wrong123!"
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    """AUTH-02c: Non-existent user returns 401."""
    resp = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com", "password": "Test1234!"
    })
    assert resp.status_code == 401


# ─── AUTH-07: Login Lock ───

@pytest.mark.asyncio
async def test_login_lock_after_5_failures(client):
    """AUTH-07: 6th failed attempt returns 429."""
    email = "lockme@example.com"
    password_correct = "Correct123!"
    password_wrong = "Wrong123!"

    # Register and verify user exists
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": password_correct
    })

    # 5 wrong password attempts
    for i in range(5):
        resp = await client.post("/api/v1/auth/login", json={
            "email": email, "password": password_wrong
        })
        assert resp.status_code == 401, f"Attempt {i+1}: expected 401, got {resp.status_code}"

    # 6th attempt should be locked
    resp = await client.post("/api/v1/auth/login", json={
        "email": email, "password": password_wrong
    })
    assert resp.status_code == 429, f"Expected 429 (locked), got {resp.status_code}: {resp.json()}"


# ─── TENANT-02: Tenant Isolation ───

@pytest.mark.asyncio
async def test_tenant_isolation(client):
    """TENANT-02: Users can only see their own datasources."""
    # Register two users
    await client.post("/api/v1/auth/register", json={
        "email": "alice@tenant.com", "password": "Test1234!"
    })
    await client.post("/api/v1/auth/register", json={
        "email": "bob@tenant.com", "password": "Test1234!"
    })

    # Login as Alice
    resp = await client.post("/api/v1/auth/login", json={
        "email": "alice@tenant.com", "password": "Test124!"
    })
    # Try with correct password
    resp = await client.post("/api/v1/auth/login", json={
        "email": "alice@tenant.com", "password": "Test1234!"
    })
    alice_token = resp.json()["access_token"]

    # Login as Bob
    resp = await client.post("/api/v1/auth/login", json={
        "email": "bob@tenant.com", "password": "Test1234!"
    })
    bob_token = resp.json()["access_token"]

    # Alice creates a datasource
    await client.post("/api/v1/datasources", json={
        "name": "Alice's DB", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "alice_db",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {alice_token}"})

    # Alice should see 1 datasource
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {alice_token}"
    })
    assert resp.status_code == 200
    alice_ds = resp.json()
    assert len(alice_ds) == 1, f"Alice should see 1 datasource, got {len(alice_ds)}"

    # Bob should see 0 datasources
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {bob_token}"
    })
    assert resp.status_code == 200
    bob_ds = resp.json()
    assert len(bob_ds) == 0, f"Bob should see 0 datasources, got {len(bob_ds)}"


# ─── AUTH-06: Password Reset ───

@pytest.mark.asyncio
async def test_password_reset_endpoint(client):
    """AUTH-06: Password reset returns 200 (even for non-existent email)."""
    resp = await client.post("/api/v1/auth/reset-password", json={
        "email": "nobody@example.com"
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_existing_user(client):
    """AUTH-06b: Password reset for existing user returns 200."""
    await client.post("/api/v1/auth/register", json={
        "email": "reset@example.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/reset-password", json={
        "email": "reset@example.com"
    })
    assert resp.status_code == 200


# ─── AUTH-03: Token Refresh ───

@pytest.mark.asyncio
async def test_token_refresh(client):
    """AUTH-03: Valid refresh token returns new access token."""
    await client.post("/api/v1/auth/register", json={
        "email": "refresh@example.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "refresh@example.com", "password": "Test1234!"
    })
    refresh_token = resp.json()["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": refresh_token
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()
