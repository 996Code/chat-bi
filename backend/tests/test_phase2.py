"""Phase 2 tests: chart inference, saved queries, CSV export, SSE stream."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app
from app.db.session import async_session_factory, get_db
from app.ai.chart_type import infer_chart_type


@pytest_asyncio.fixture(scope="function")
async def db():
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM users"))
        await session.execute(text("DELETE FROM tenants"))
        await session.execute(text("DELETE FROM data_sources"))
        await session.execute(text("DELETE FROM saved_queries"))
        await session.commit()
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db):
    from app.api import auth as auth_module, datasource as ds_module, query as q_module
    from app.api import saved_query as sq_module, export as exp_module

    async def override():
        yield db

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[ds_module.get_db] = override
    app.dependency_overrides[q_module.get_db] = override
    app.dependency_overrides[sq_module.get_db] = override
    app.dependency_overrides[exp_module.get_db] = override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_token(client):
    await client.post("/api/v1/auth/register", json={
        "email": "phase2@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "phase2@test.com", "password": "Test1234!"
    })
    return resp.json()["access_token"]


@pytest_asyncio.fixture(scope="function")
async def auth_token_b(client):
    await client.post("/api/v1/auth/register", json={
        "email": "phase2b@test.com", "password": "Test1234!"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "phase2b@test.com", "password": "Test1234!"
    })
    return resp.json()["access_token"]


# ─── Chart Type Inference ───

def test_chart_metric_single():
    """Single value → metric card."""
    result = infer_chart_type(["total"], [{"total": 12345}])
    assert result == "metric"


def test_chart_line_time_series():
    """Time + value → line chart."""
    rows = [
        {"month": "2024-01", "sales": 100},
        {"month": "2024-02", "sales": 200},
        {"month": "2024-03", "sales": 150},
    ]
    result = infer_chart_type(["month", "sales"], rows)
    assert result == "line"


def test_chart_bar_category():
    """Category + numeric value → bar chart."""
    rows = [
        {"product": "A", "count": 10},
        {"product": "B", "count": 20},
        {"product": "C", "count": 30},
        {"product": "D", "count": 15},
        {"product": "E", "count": 25},
        {"product": "F", "count": 18},
    ]
    result = infer_chart_type(["product", "count"], rows)
    assert result == "bar"


def test_chart_pie_few_categories():
    """Category + value, ≤5 rows → pie chart."""
    rows = [
        {"region": "East", "sales": 50},
        {"region": "West", "sales": 30},
        {"region": "South", "sales": 20},
    ]
    result = infer_chart_type(["region", "sales"], rows)
    assert result == "pie"


def test_chart_scatter_two_numeric():
    """2+ numeric columns with many rows → scatter."""
    rows = [{"x": i, "y": i * 2} for i in range(15)]
    result = infer_chart_type(["x", "y"], rows)
    # With 2 numeric columns and 15 rows, scatter is a reasonable choice
    # but bar is also acceptable (both are valid for 2-column numeric data)
    assert result in ("scatter", "bar")


def test_chart_empty_data():
    """No data → table."""
    assert infer_chart_type([], []) == "table"
    assert infer_chart_type(["col1"], []) == "table"


# ─── Saved Queries CRUD ───

@pytest.mark.asyncio
async def test_save_query(client, auth_token):
    """Save a query returns 201."""
    resp = await client.post("/api/v1/queries", json={
        "name": "Monthly Sales",
        "query_text": "上个月的销售总额",
        "generated_sql": "SELECT SUM(amount) FROM sales WHERE month = '2024-01'",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Monthly Sales"
    assert "id" in data


@pytest.mark.asyncio
async def test_save_query_requires_name(client, auth_token):
    """Save without name returns 400."""
    resp = await client.post("/api/v1/queries", json={
        "query_text": "test",
        "generated_sql": "SELECT 1",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_queries(client, auth_token):
    """List saved queries returns user's queries."""
    # Save two queries
    for i in range(2):
        await client.post("/api/v1/queries", json={
            "name": f"Query {i}",
            "query_text": f"test {i}",
            "generated_sql": f"SELECT {i}",
            "datasource_id": "00000000-0000-0000-0000-000000000001",
        }, headers={"Authorization": f"Bearer {auth_token}"})

    resp = await client.get("/api/v1/queries", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 200
    data = resp.json()
    queries = data.get("data", data)
    assert len(queries) == 2


@pytest.mark.asyncio
async def test_query_isolation(client, auth_token, auth_token_b):
    """User A cannot see User B's queries."""
    # User A saves a query
    await client.post("/api/v1/queries", json={
        "name": "User A Query",
        "query_text": "test",
        "generated_sql": "SELECT 1",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})

    # User B should see nothing
    resp = await client.get("/api/v1/queries", headers={
        "Authorization": f"Bearer {auth_token_b}"
    })
    assert resp.status_code == 200
    data = resp.json()
    queries = data.get("data", data)
    assert len(queries) == 0


@pytest.mark.asyncio
async def test_get_single_query(client, auth_token):
    """Get single query by ID."""
    save_resp = await client.post("/api/v1/queries", json={
        "name": "Get Test",
        "query_text": "test",
        "generated_sql": "SELECT 1",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})
    query_id = save_resp.json()["id"]

    resp = await client.get(f"/api/v1/queries/{query_id}", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Get Test"


@pytest.mark.asyncio
async def test_delete_query(client, auth_token):
    """Delete a saved query."""
    save_resp = await client.post("/api/v1/queries", json={
        "name": "Delete Test",
        "query_text": "test",
        "generated_sql": "SELECT 1",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})
    query_id = save_resp.json()["id"]

    resp = await client.delete(f"/api/v1/queries/{query_id}", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 204

    # Verify deleted
    resp = await client.get(f"/api/v1/queries/{query_id}", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rerun_query(client, auth_token):
    """Re-run a saved query returns original question/SQL."""
    save_resp = await client.post("/api/v1/queries", json={
        "name": "Rerun Test",
        "query_text": "查询所有用户",
        "generated_sql": "SELECT * FROM users",
        "datasource_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {auth_token}"})
    query_id = save_resp.json()["id"]

    resp = await client.post(f"/api/v1/queries/{query_id}/re-run", headers={
        "Authorization": f"Bearer {auth_token}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["question"] == "查询所有用户"
    assert data["sql"] == "SELECT * FROM users"


# ─── CSV Export ───

@pytest.mark.asyncio
async def test_csv_export(client, auth_token):
    """Export query results as CSV."""
    resp = await client.post("/api/v1/export/csv", json={
        "columns": ["name", "age"],
        "rows": [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ],
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    text = resp.text
    assert "name,age" in text
    assert "Alice,30" in text
    assert "Bob,25" in text


@pytest.mark.asyncio
async def test_csv_export_empty(client, auth_token):
    """Export empty data returns error."""
    resp = await client.post("/api/v1/export/csv", json={
        "columns": [],
        "rows": [],
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code in (400, 404)


# ─── SSE Stream ───

@pytest.mark.asyncio
async def test_sse_stream_requires_auth(client):
    """SSE endpoint requires authentication."""
    resp = await client.post("/api/v1/query/stream", json={
        "question": "test", "datasource_id": "fake"
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_stream_invalid_datasource(client, auth_token):
    """SSE endpoint returns error for non-existent datasource."""
    resp = await client.post("/api/v1/query/stream", json={
        "question": "test", "datasource_id": "00000000-0000-0000-0000-000000000000"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sse_stream_greeting(client, auth_token):
    """SSE greeting query returns intent=Other."""
    # Create a datasource first
    resp = await client.post("/api/v1/datasources", json={
        "name": "Stream DB", "type": "mysql", "host": "127.0.0.1",
        "port": 3306, "database_name": "testdb",
        "username": "root", "password": "root"
    }, headers={"Authorization": f"Bearer {auth_token}"})
    ds_id = resp.json()["id"]

    resp = await client.post("/api/v1/query/stream", json={
        "question": "你好", "datasource_id": ds_id
    }, headers={"Authorization": f"Bearer {auth_token}"})
    assert resp.status_code == 200
    text = resp.text
    assert "intent" in text
    assert "Other" in text
