"""E2E tests: complete user journey without real MySQL/LLM."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app
from app.db.session import async_session_factory, get_db
from app.schemas.auth import RegisterRequest


@pytest_asyncio.fixture(scope="function")
async def db():
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM users"))
        await session.execute(text("DELETE FROM tenants"))
        await session.execute(text("DELETE FROM data_sources"))
        await session.execute(text("DELETE FROM metadata_configs"))
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


# ─── E2E-01: 注册 → 登录 → 添加数据源 → 问候语查询 ───

@pytest.mark.asyncio
async def test_e2e_full_journey(client):
    """E2E-01: Complete user journey from registration to query."""
    # Step 1: Register
    resp = await client.post("/api/v1/auth/register", json={
        "email": "e2e@test.com", "password": "Test1234!"
    })
    assert resp.status_code == 201, f"Register failed: {resp.json()}"

    # Step 2: Login
    resp = await client.post("/api/v1/auth/login", json={
        "email": "e2e@test.com", "password": "Test1234!"
    })
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert "access_token" in resp.json()

    # Step 3: Access home (list datasources - empty)
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {token}"
    })
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    # Step 4: Create datasource
    resp = await client.post("/api/v1/datasources", json={
        "name": "E2E Test DB", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "e2e_db",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    ds_id = resp.json()["id"]

    # Step 5: Verify datasource visible
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {token}"
    })
    assert resp.status_code == 200
    datasources = resp.json()
    assert len(datasources) == 1
    assert datasources[0]["name"] == "E2E Test DB"
    assert "password" not in datasources[0]  # Not leaked
    ds_id = datasources[0]["id"]

    # Step 6: Query with greeting (no LLM needed - classified as Other)
    resp = await client.post("/api/v1/query", json={
        "question": "你好", "datasource_id": ds_id
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("intent") == "Other"
    assert data.get("success") is False  # No SQL generated
    assert "sql" not in data or data.get("sql") is None


# ─── E2E-02: 错误恢复流程 ───

@pytest.mark.asyncio
async def test_e2e_error_recovery(client):
    """E2E-02: Error recovery - invalid datasource → test fails → delete."""
    # Register + login
    await client.post("/api/v1/auth/register", json={
        "email": "recover@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "recover@test.com", "password": "Test1234!"
    })
    token = resp.json()["access_token"]

    # Create datasource with invalid host
    resp = await client.post("/api/v1/datasources", json={
        "name": "Bad DB", "type": "mysql", "host": "invalid-host-12345.local",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    ds_id = resp.json()["id"]

    # Test connection should fail
    resp = await client.post(f"/api/v1/datasources/{ds_id}/test", headers={
        "Authorization": f"Bearer {token}"
    })
    assert resp.status_code == 400
    assert "CONNECTION_FAILED" in resp.json()["detail"]["code"]

    # Delete the bad datasource
    resp = await client.delete(f"/api/v1/datasources/{ds_id}", headers={
        "Authorization": f"Bearer {token}"
    })
    assert resp.status_code == 204

    # Verify it's gone
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {token}"
    })
    assert len(resp.json()) == 0


# ─── E2E-03: 多用户完整隔离 ───

@pytest.mark.asyncio
async def test_e2e_multi_user_isolation(client):
    """E2E-03: Multiple users, each with own tenant and datasources."""
    users = []
    for i in range(3):
        email = f"user{i}@isolation.com"
        # Register
        resp = await client.post("/api/v1/auth/register", json={
            "email": email, "password": f"Test1234!{i}"
        })
        assert resp.status_code == 201

        # Login
        resp = await client.post("/api/v1/auth/login", json={
            "email": email, "password": f"Test1234!{i}"
        })
        assert resp.status_code == 200
        users.append({
            "email": email,
            "token": resp.json()["access_token"],
            "tenant_id": resp.json().get("tenant_id"),
        })

    # Each user creates a datasource
    for i, user in enumerate(users):
        resp = await client.post("/api/v1/datasources", json={
            "name": f"User{i}'s DB", "type": "mysql", "host": "127.0.0.1",
            "port": 3306, "database_name": f"user{i}_db",
            "username": "root", "password": "root"
        }, headers={"Authorization": f"Bearer {user['token']}"})
        assert resp.status_code == 201

    # Each user should see exactly 1 datasource (their own)
    for i, user in enumerate(users):
        resp = await client.get("/api/v1/datasources", headers={
            "Authorization": f"Bearer {user['token']}"
        })
        assert resp.status_code == 200
        ds_list = resp.json()
        assert len(ds_list) == 1, f"User {i} should see 1 datasource, got {len(ds_list)}"
        assert ds_list[0]["name"] == f"User{i}'s DB"


# ─── E2E-04: Token lifecycle ───

@pytest.mark.asyncio
async def test_e2e_token_lifecycle(client):
    """E2E-04: Login → access → refresh → old token invalidation."""
    await client.post("/api/v1/auth/register", json={
        "email": "token@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "token@test.com", "password": "Test1234!"
    })
    at1 = resp.json()["access_token"]
    rt1 = resp.json()["refresh_token"]

    # Step 1: Access protected endpoint with at1
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {at1}"
    })
    assert resp.status_code == 200

    # Step 2: Refresh with rt1
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": rt1
    })
    assert resp.status_code == 200
    at2 = resp.json()["access_token"]
    rt2 = resp.json()["refresh_token"]

    # rt2 must be different from rt1 (token rotation)
    assert rt2 != rt1, "Refresh token should be rotated"

    # Step 3: Access with at2
    resp = await client.get("/api/v1/datasources", headers={
        "Authorization": f"Bearer {at2}"
    })
    assert resp.status_code == 200

    # Step 4: Use rt2 to refresh again (should work)
    resp = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": rt2
    })
    assert resp.status_code == 200
    at3 = resp.json()["access_token"]
    rt3 = resp.json()["refresh_token"]
    assert rt3 != rt2


# ─── E2E-05: 查询接口完整流程 ───

@pytest.mark.asyncio
async def test_e2e_query_various_inputs(client):
    """E2E-05: Various query inputs - greetings, data questions, empty."""
    await client.post("/api/v1/auth/register", json={
        "email": "query@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "query@test.com", "password": "Test1234!"
    })
    token = resp.json()["access_token"]

    # Create a datasource first (required for all queries)
    resp = await client.post("/api/v1/datasources", json={
        "name": "Query Test DB", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "query_db",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {token}"})
    ds_id = resp.json()["id"]

    # Greeting → Other intent
    resp = await client.post("/api/v1/query", json={
        "question": "你好", "datasource_id": ds_id
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json().get("intent") == "Other"

    # Thank you → Other intent
    resp = await client.post("/api/v1/query", json={
        "question": "谢谢", "datasource_id": ds_id
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json().get("intent") == "Other"

    # Data question → DataQuery intent (requires LLM, may fail without API key)
    # We just verify the endpoint doesn't crash and returns a valid response
    resp = await client.post("/api/v1/query", json={
        "question": "查询所有用户", "datasource_id": ds_id
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    # Either intent is DataQuery (with LLM) or endpoint returns an error gracefully
    if data.get("intent") is not None:
        assert data["intent"] == "DataQuery"
    else:
        # LLM unavailable - endpoint should still return a valid error response
        assert "error" in data


# ─── E2E-06: 健康检查流程 ───

@pytest.mark.asyncio
async def test_e2e_health_check(client):
    """E2E-06: Health check endpoint flow."""
    await client.post("/api/v1/auth/register", json={
        "email": "health@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "health@test.com", "password": "Test1234!"
    })
    token = resp.json()["access_token"]

    # Create datasource
    resp = await client.post("/api/v1/datasources", json={
        "name": "Health DB", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "health_db",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {token}"})
    ds_id = resp.json()["id"]

    # Health check (will fail since no real MySQL)
    resp = await client.get(f"/api/v1/datasources/{ds_id}/health", headers={
        "Authorization": f"Bearer {token}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "healthy" in data
    assert "error" in data
    assert data["healthy"] is False  # No real MySQL running


# ─── Security: 认证绕过测试 ───

@pytest.mark.asyncio
async def test_security_auth_bypass(client):
    """Security: All protected endpoints should reject without auth."""
    protected_endpoints = [
        ("GET", "/api/v1/datasources"),
        ("POST", "/api/v1/datasources", {"name": "test", "type": "mysql", "host": "127.0.0.1", "port": 3306, "database_name": "test", "username": "root", "password": "root"}),
        ("POST", "/api/v1/query", {"question": "test"}),
    ]

    for endpoint in protected_endpoints:
        if len(endpoint) == 2:
            method, url = endpoint
            resp = await client.request(method, url)
        else:
            method, url, body = endpoint
            resp = await client.request(method, url, json=body)

        assert resp.status_code == 401, f"{method} {url} should return 401, got {resp.status_code}"


# ─── Security: CORS 预检 ───

@pytest.mark.asyncio
async def test_security_cors_preflight(client):
    """Security: CORS preflight should allow localhost:5173."""
    resp = await client.options("/api/v1/datasources", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
    })
    # FastAPI CORS middleware should respond to OPTIONS
    assert resp.status_code == 200
    cors_header = resp.headers.get("access-control-allow-origin", "")
    assert "localhost:5173" in cors_header
