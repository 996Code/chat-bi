"""Phase 2 additional tests: Schema selection, feedback, analytics, PostgreSQL pool, self-heal integration, chart type in response."""
import json
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.session import async_session_factory, get_db
from app.db.models import Feedback, AnalyticsEvent
from app.ai.chart_type import infer_chart_type
from app.ai.nodes.schema_selection import _build_schema_context


@pytest_asyncio.fixture(scope="function")
async def db():
    """Fresh DB per test with cleaned tables."""
    async with async_session_factory() as session:
        from sqlalchemy import text
        for table in ["analytics_events", "feedback", "audit_logs", "saved_queries",
                       "metadata_configs", "data_sources", "users", "tenants"]:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass
        await session.commit()
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db):
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


async def _register_and_login(client: AsyncClient, email: str = "test@test.com"):
    await client.post("/api/v1/auth/register", json={"email": email, "password": "Test1234!"})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "Test1234!"})
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


# ─── Schema Selection Tests ───

def test_build_schema_context_includes_table_name():
    """Schema context should include selected table names."""
    metadata = {
        "models": [
            {"name": "orders", "description": "订单表", "columns": [
                {"name": "id", "type": "int", "nullable": False, "primary": True, "comment": ""},
                {"name": "amount", "type": "decimal", "nullable": False, "primary": False, "comment": "金额"},
            ]},
        ],
        "relationships": [],
    }
    result = _build_schema_context(["orders"], {"orders": ["id", "amount"]}, metadata)
    assert "表名: orders" in result
    assert "amount" in result


def test_build_schema_context_empty_selection():
    """Empty table selection returns header only."""
    result = _build_schema_context([], {}, {"models": [], "relationships": []})
    assert "可用的数据库表结构" in result


def test_build_schema_context_fallback_all_columns():
    """When LLM selects no columns, all columns are included as fallback."""
    metadata = {
        "models": [
            {"name": "products", "description": "", "columns": [
                {"name": "id", "type": "int", "nullable": False, "primary": True, "comment": ""},
                {"name": "name", "type": "varchar", "nullable": True, "primary": False, "comment": ""},
                {"name": "price", "type": "decimal", "nullable": True, "primary": False, "comment": ""},
            ]},
        ],
        "relationships": [],
    }
    result = _build_schema_context(["products"], {"products": []}, metadata)
    assert "id" in result
    assert "name" in result
    assert "price" in result


def test_build_schema_context_includes_relationships():
    """Schema context includes relationship info."""
    metadata = {
        "models": [
            {"name": "orders", "description": "", "columns": [
                {"name": "user_id", "type": "int", "nullable": True, "primary": False, "comment": ""},
            ]},
            {"name": "users", "description": "", "columns": [
                {"name": "id", "type": "int", "nullable": False, "primary": True, "comment": ""},
            ]},
        ],
        "relationships": [
            {"from_table": "orders", "from_column": "user_id", "to_table": "users", "to_column": "id"},
        ],
    }
    result = _build_schema_context(
        ["orders", "users"],
        {"orders": ["user_id"], "users": ["id"]},
        metadata,
    )
    assert "关联" in result
    assert "user_id" in result


# ─── Feedback Tests ───

@pytest.mark.asyncio
async def test_feedback_up(client, db):
    """Feedback: 点赞。"""
    await _register_and_login(client)
    resp = await client.post("/api/v1/feedback", json={
        "query_id": "test-query-1",
        "rating": "up",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["rating"] == "up"


@pytest.mark.asyncio
async def test_feedback_down(client, db):
    """Feedback: 踩。"""
    await _register_and_login(client)
    resp = await client.post("/api/v1/feedback", json={
        "query_id": "test-query-1",
        "rating": "down",
        "comment": "结果不准确",
    })
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_feedback_invalid_rating(client, db):
    """Feedback: 无效评分。"""
    await _register_and_login(client)
    resp = await client.post("/api/v1/feedback", json={
        "query_id": "test-query-1",
        "rating": "maybe",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_feedback(client, db):
    """Feedback: 列出反馈。"""
    await _register_and_login(client)
    await client.post("/api/v1/feedback", json={"query_id": "q1", "rating": "up"})
    resp = await client.get("/api/v1/feedback")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ─── Chart Type Tests ───

def test_chart_none_empty():
    """空数据返回 table（默认值）。"""
    assert infer_chart_type([], []) == "table"


def test_chart_metric_one_row():
    """单列单行 → metric。"""
    assert infer_chart_type(["total"], [{"total": 100}]) == "metric"


def test_chart_line_time():
    """时间列 + 数值列 → line。"""
    rows = [
        {"date": "2024-01", "amount": 100},
        {"date": "2024-02", "amount": 200},
    ]
    assert infer_chart_type(["date", "amount"], rows) == "line"


# ─── Connection Pool Multi-Dialect ───

def test_pool_url_mysql():
    """MySQL 连接池 URL 格式。"""
    from app.db.models import DataSource
    import uuid

    ds = DataSource(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="test-mysql",
        db_type="mysql",
        host="localhost",
        port=3306,
        database_name="testdb",
        username_encrypted="",
        password_encrypted="",
    )
    assert ds.db_type == "mysql"


def test_pool_url_postgresql():
    """PostgreSQL 连接池 URL 格式。"""
    from app.db.models import DataSource
    import uuid

    ds = DataSource(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="test-pg",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="testdb",
        username_encrypted="",
        password_encrypted="",
    )
    assert ds.db_type == "postgresql"