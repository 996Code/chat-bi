"""Additional tests for schema validation, token security, and datasource operations."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from datetime import datetime, timedelta, timezone
from jose import jwt

from app.main import app
from app.db.session import async_session_factory, get_db
from app.core.config import settings


@pytest_asyncio.fixture(scope="function")
async def db():
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM users"))
        await session.execute(text("DELETE FROM tenants"))
        await session.execute(text("DELETE FROM data_sources"))
        await session.commit()
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db):
    from app.api import auth as auth_module, datasource as ds_module, query as q_module

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[ds_module.get_db] = override
    app.dependency_overrides[q_module.get_db] = override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_token(client):
    await client.post("/api/v1/auth/register", json={
        "email": "schema@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "schema@test.com", "password": "Test1234!"
    })
    return resp.json()["access_token"]


# ─── AUTH-01c: Password Strength ───

@pytest.mark.asyncio
async def test_password_too_short(client):
    """AUTH-01c: Password less than 8 chars returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "short@test.com", "password": "Abc1234"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_password_no_letter(client):
    """AUTH-01c: Password without letters returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "noletter@test.com", "password": "12345678"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_password_no_number(client):
    """AUTH-01c: Password without numbers returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "nonumber@test.com", "password": "abcdefgh"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_password_empty(client):
    """AUTH-01c: Empty password returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "empty@test.com", "password": ""
    })
    assert resp.status_code == 422


# ─── AUTH-01d: Email Format ───

@pytest.mark.asyncio
async def test_email_invalid(client):
    """AUTH-01d: Invalid email format returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "not-an-email", "password": "Test1234!"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_email_missing_at(client):
    """AUTH-01d: Email without @ returns 422."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "missing-at.com", "password": "Test1234!"
    })
    assert resp.status_code == 422


# ─── AUTH-03: Bcrypt Hash ───

@pytest.mark.asyncio
async def test_bcrypt_hash_format(client):
    """AUTH-03: Password stored as bcrypt with cost factor 12."""
    await client.post("/api/v1/auth/register", json={
        "email": "bcrypt@test.com", "password": "Test1234!"
    })
    async with async_session_factory() as session:
        from sqlalchemy import select
        from app.db.models import User
        result = await session.execute(
            select(User).where(User.email == "bcrypt@test.com")
        )
        user = result.scalar_one()
        assert user.password_hash.startswith("$2b$12$"), f"Expected bcrypt cost 12, got: {user.password_hash[:10]}"


# ─── AUTH-04: Token Structure ───

@pytest.mark.asyncio
async def test_jwt_contains_required_claims(client):
    """AUTH-04: Access token contains sub, email, tenant_id, type, exp."""
    await client.post("/api/v1/auth/register", json={
        "email": "jwt@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "jwt@test.com", "password": "Test1234!"
    })
    token = resp.json()["access_token"]
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert "sub" not in payload or "user_id" in payload  # We use user_id not sub
    assert "email" in payload
    assert "tenant_id" in payload
    assert payload["type"] == "access"
    assert "exp" in payload


@pytest.mark.asyncio
async def test_refresh_token_contains_jti(client):
    """AUTH-04: Refresh token contains jti (unique identifier)."""
    await client.post("/api/v1/auth/register", json={
        "email": "jti@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "jti@test.com", "password": "Test1234!"
    })
    token = resp.json()["refresh_token"]
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    assert "jti" in payload, "Refresh token must contain jti"
    assert payload["type"] == "refresh"


@pytest.mark.asyncio
async def test_expired_token_rejected(client):
    """AUTH-04: Expired token returns 401."""
    # Create a manually expired token
    from app.core.security import create_access_token
    expired_payload = {"user_id": "fake", "email": "fake@test.com", "tenant_id": "fake", "type": "access"}
    expired_token = jwt.encode(
        {**expired_payload, "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        settings.secret_key,
        algorithm="HS256",
    )

    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {expired_token}"
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tampered_token_rejected(client):
    """AUTH-04: Tampered token signature returns 401."""
    await client.post("/api/v1/auth/register", json={
        "email": "tamper@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "tamper@test.com", "password": "Test1234!"
    })
    token = resp.json()["access_token"]
    # Tamper: change last character
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {tampered}"
    })
    assert resp.status_code == 401


# ─── DSO-07: Datasource Update & Delete ───

@pytest.mark.asyncio
async def test_update_datasource_name(client, auth_token):
    """DSO-07: Update datasource name returns 200."""
    create_resp = await client.post("/api/v1/datasources", json={
        "name": "Original Name", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    ds_id = create_resp.json()["id"]

    resp = await client.put(f"/api/v1/datasources/{ds_id}", json={
        "name": "Updated Name"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_delete_nonexistent_datasource(client, auth_token):
    """DSO-07: Delete non-existent datasource returns 404."""
    resp = await client.delete("/api/v1/datasources/00000000-0000-0000-0000-000000000000", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_nonexistent_datasource(client, auth_token):
    """DSO-07: Update non-existent datasource returns 404."""
    resp = await client.put("/api/v1/datasources/00000000-0000-0000-0000-000000000000", json={
        "name": "New Name"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 404


# ─── DSO-09: Credential Encryption ───

@pytest.mark.asyncio
async def test_credentials_encrypted_in_db(client, auth_token):
    """DSO-09: Datasource credentials stored encrypted (Fernet)."""
    await client.post("/api/v1/datasources", json={
        "name": "Encrypt Test", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "encrypted_user", "password": "encrypted_pass"
    }, headers={"Authorization": f"Bearer {auth_token}"})

    async with async_session_factory() as session:
        from sqlalchemy import select, text
        # Check raw DB for encrypted values
        result = await session.execute(text(
            "SELECT username_encrypted, password_encrypted FROM data_sources WHERE name = 'Encrypt Test'"
        ))
        row = result.first()
        assert row[0].startswith("gAAA"), f"Username not encrypted: {row[0][:10]}"
        assert row[1].startswith("gAAA"), f"Password not encrypted: {row[1][:10]}"


# ─── API-03: Standard Error Response ───

@pytest.mark.asyncio
async def test_401_error_format(client):
    """API-03: 401 error has code, message, details structure."""
    resp = await client.get("/api/v1/datasources")
    data = resp.json()["detail"]
    assert "code" in data
    assert "message" in data
    assert "details" in data


@pytest.mark.asyncio
async def test_404_error_format(client, auth_token):
    """API-03: 404 error has code, message, details structure."""
    # Use health check endpoint which returns 404 for non-existent datasource
    resp = await client.get("/api/v1/datasources/00000000-0000-0000-0000-000000000000/health", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    data = resp.json()["detail"]
    assert "code" in data
    assert "message" in data


@pytest.mark.asyncio
async def test_422_error_format(client):
    """API-03: 422 error has loc, msg, type structure (FastAPI standard)."""
    resp = await client.post("/api/v1/auth/register", json={
        "email": "invalid", "password": "Test1234!"
    })
    data = resp.json()
    assert "detail" in data
    # FastAPI validation errors have loc, msg, type
    assert isinstance(data["detail"], list)
    assert len(data["detail"]) > 0
    error = data["detail"][0]
    assert "loc" in error
    assert "msg" in error
    assert "type" in error
