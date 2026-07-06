"""
ChatBI v2 — Saved Query CSV Export Tests (B3: 白名单列校验)

覆盖:
  - 无语义层时 fail-closed 拒绝导出
  - 有语义层时白名单列校验生效 (非白名单列 → 422)
  - 正常导出 (白名单列内 SQL → 校验通过)
  - 数据源不存在 → 404
  - SavedQuery 不存在 → 404
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.models import DataSource, SavedQuery, SemanticModel


def _auth_header(user_id: str = "admin_1", tenant_id: str = "tenant_A", role: str = "admin") -> dict:
    token = create_access_token({
        "user_id": user_id, "email": f"{user_id}@test.com",
        "tenant_id": tenant_id, "role": role,
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth():
    return _auth_header()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _api(path: str = "") -> str:
    prefix = get_settings().api_prefix
    return f"{prefix}/saved-queries{path}"


async def _create_datasource(client, auth) -> str:
    """创建测试数据源 (PG chatbi_test 库本身, 可执行 SELECT 1)。
    返回数据源的实际 id (由后端自动生成)。
    """
    resp = await client.post(
        f"{get_settings().api_prefix}/data-sources",
        json={
            "name": "CSV测试数据源",
            "db_type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "chatbi_test",
            "username": "root",
            "password": "root",
        },
        headers=auth,
    )
    assert resp.status_code in (201, 200)
    return resp.json()["id"]


async def _create_saved_query(db_session, sql: str = "SELECT 1 AS col_a",
                               tenant_id: str = "tenant_A", user_id: str = "admin_1") -> str:
    """通过 db_session 创建 SavedQuery 并 commit (HTTP 可见)。"""
    q = SavedQuery(
        tenant_id=tenant_id,
        user_id=user_id,
        question="测试查询",
        sql_text=sql,
    )
    db_session.add(q)
    await db_session.commit()
    return q.id


async def _create_semantic_model(db_session, data_source_id: str, tenant_id: str = "tenant_A",
                                  columns: list[str] | None = None) -> str:
    """通过 db_session 创建语义层并 commit (HTTP 可见)。"""
    col_defs = [
        {"name": c, "display_name": c, "data_type": "TEXT", "source": "manual", "confidence": 1.0}
        for c in (columns or ["col_a"])
    ]
    content = {
        "version": 1,
        "models": [{
            "name": "test_table",
            "display_name": "测试表",
            "source": "manual",
            "columns": col_defs,
        }],
        "sample_questions": [],
    }
    sm = SemanticModel(
        tenant_id=tenant_id,
        data_source_id=data_source_id,
        version=1,
        content=content,
        is_current=True,
    )
    db_session.add(sm)
    await db_session.commit()
    return sm.id


class TestCsvExportWhitelist:
    """B3: CSV 导出白名单列校验。"""

    @pytest.mark.asyncio
    async def test_no_semantic_model_rejects_export(self, client, auth, db_session):
        """无语义层 → fail-closed 拒绝导出 (422)。"""
        ds_id = await _create_datasource(client, auth)
        sq_id = await _create_saved_query(db_session, sql="SELECT 1 AS col_a")

        resp = await client.get(
            _api(f"/{sq_id}/export"),
            params={"data_source_id": ds_id},
            headers=auth,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "语义层" in detail or "fail-closed" in detail

    @pytest.mark.asyncio
    async def test_column_not_in_whitelist_rejected(self, client, auth, db_session):
        """SQL 引用非白名单列 → 422 (Layer3 校验生效)。"""
        ds_id = await _create_datasource(client, auth)
        # 语义层只有 col_a
        await _create_semantic_model(db_session, ds_id, columns=["col_a"])
        # SQL 引用 col_a + col_secret (不在白名单)
        sq_id = await _create_saved_query(db_session, sql="SELECT col_a, col_secret FROM test_table")

        resp = await client.get(
            _api(f"/{sq_id}/export"),
            params={"data_source_id": ds_id},
            headers=auth,
        )
        assert resp.status_code == 422
        assert "校验失败" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_column_in_whitelist_passes(self, client, auth, db_session):
        """SQL 只引用白名单列 → 通过校验 (执行可能因表不存在失败, 但校验通过)。"""
        ds_id = await _create_datasource(client, auth)
        await _create_semantic_model(db_session, ds_id, columns=["col_a"])
        sq_id = await _create_saved_query(db_session, sql="SELECT 1 AS col_a")

        resp = await client.get(
            _api(f"/{sq_id}/export"),
            params={"data_source_id": ds_id},
            headers=auth,
        )
        # 校验通过 → 执行 SQL → 可能 500 (test_table 不存在) 或 200
        # 关键: 不是 422 (校验失败)
        assert resp.status_code != 422

    @pytest.mark.asyncio
    async def test_datasource_not_found(self, client, auth, db_session):
        """数据源不存在 → 404。"""
        sq_id = await _create_saved_query(db_session)
        resp = await client.get(
            _api(f"/{sq_id}/export"),
            params={"data_source_id": "nonexistent_ds"},
            headers=auth,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_saved_query_not_found(self, client, auth):
        """SavedQuery 不存在 → 404。"""
        resp = await client.get(
            _api("/nonexistent_sq/export"),
            params={"data_source_id": "ds1"},
            headers=auth,
        )
        assert resp.status_code == 404
