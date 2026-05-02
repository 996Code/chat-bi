"""Tests for datasource CRUD and query pipeline."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app
from app.db.session import async_session_factory, get_db


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

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[ds_module.get_db] = override_get_db
    app.dependency_overrides[q_module.get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_token(client):
    await client.post("/api/v1/auth/register", json={
        "email": "dsuser@example.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "dsuser@example.com", "password": "Test1234!"
    })
    return resp.json()["access_token"]


# ─── DSO-01: Datasource CRUD ───

@pytest.mark.asyncio
async def test_create_datasource(client, auth_token):
    """DSO-01a: Create datasource returns 201."""
    resp = await client.post("/api/v1/datasources", json={
        "name": "My MySQL", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_datasources(client, auth_token):
    """DSO-01b: List returns created datasources."""
    await client.post("/api/v1/datasources", json={
        "name": "List Test", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {auth_token}"})

    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_list_datasources_unauthenticated(client):
    """DSO-01c: Unauthenticated list returns 401."""
    resp = await client.get("/api/v1/datasources")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_datasource(client, auth_token):
    """DSO-01d: Delete datasource returns 200."""
    create_resp = await client.post("/api/v1/datasources", json={
        "name": "To Delete", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    ds_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/datasources/{ds_id}", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 204

    # Verify deleted
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert len(resp.json()) == 0


# ─── DSO-03: Datasource Health Check ───

@pytest.mark.asyncio
async def test_health_check_not_found(client, auth_token):
    """DSO-03: Health check for non-existent datasource returns 404."""
    resp = await client.get("/api/v1/datasources/00000000-0000-0000-0000-000000000000/health", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 404


# ─── QRY-01: Query Module ───

@pytest.mark.asyncio
async def test_query_requires_auth(client):
    """QRY-01a: Query without auth returns 401."""
    resp = await client.post("/api/v1/query", json={"question": "test"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_query_requires_question(client, auth_token):
    """QRY-01b: Query with empty question returns 422 (Pydantic validation)."""
    resp = await client.post("/api/v1/query", json={"question": ""}, headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 422


# ─── QRY-02: SQL Injection Protection ───

@pytest.mark.asyncio
async def test_sql_injection_drop(client, auth_token):
    """QRY-02: DROP TABLE should be rejected by SQLGlot validation."""
    resp = await client.post("/api/v1/query", json={
        "question": "DROP TABLE users"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    # Should either return an error in the response body or reject at the SQL validation level
    data = resp.json()
    if resp.status_code == 200:
        assert "error" in data or data.get("sql") is None or "error" in data.get("sql", "").lower()
    else:
        # 4xx is also acceptable as rejection
        assert resp.status_code in (400, 403, 422)


@pytest.mark.asyncio
async def test_sql_injection_semicolon(client, auth_token):
    """QRY-02b: Semicolon injection should be rejected."""
    resp = await client.post("/api/v1/query", json={
        "question": "SELECT * FROM users; DROP TABLE users"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    data = resp.json()
    if resp.status_code == 200:
        sql = data.get("sql", "")
        # Should not contain DROP
        assert "drop" not in sql.lower(), f"SQL contains DROP: {sql}"
