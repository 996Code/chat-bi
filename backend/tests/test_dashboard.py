"""
ChatBI v2 — Dashboard API Tests (T067)

覆盖:
  - Dashboard CRUD: create / list / update / delete
  - Widget CRUD: add / delete / layout
  - 租户隔离
  - 边界校验: 空名称 / 不存在的看板 / 不存在的 widget
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import create_access_token


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
def auth_b():
    """不同租户的认证头。"""
    return _auth_header(user_id="admin_2", tenant_id="tenant_B", role="admin")


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _api(path: str = "") -> str:
    prefix = get_settings().api_prefix
    return f"{prefix}/dashboards{path}"


class TestDashboardCRUD:
    """看板 CRUD。"""

    @pytest.mark.asyncio
    async def test_create_dashboard(self, client, auth):
        resp = await client.post(_api(), json={"name": "测试看板"}, headers=auth)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "测试看板"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_dashboard_empty_name(self, client, auth):
        """空名称 → 422 (Pydantic 验证)。"""
        resp = await client.post(_api(), json={"name": ""}, headers=auth)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_dashboards(self, client, auth):
        # 先创建两个
        await client.post(_api(), json={"name": "看板A"}, headers=auth)
        await client.post(_api(), json={"name": "看板B"}, headers=auth)
        resp = await client.get(_api(), headers=auth)
        assert resp.status_code == 200
        names = [d["name"] for d in resp.json()]
        assert "看板A" in names
        assert "看板B" in names

    @pytest.mark.asyncio
    async def test_update_dashboard(self, client, auth):
        create_resp = await client.post(_api(), json={"name": "旧名"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.put(_api(f"/{dash_id}"), json={"name": "新名"}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"

    @pytest.mark.asyncio
    async def test_update_nonexistent_dashboard(self, client, auth):
        resp = await client.put(_api("/no_such_id"), json={"name": "x"}, headers=auth)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_dashboard(self, client, auth):
        create_resp = await client.post(_api(), json={"name": "待删"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.delete(_api(f"/{dash_id}"), headers=auth)
        assert resp.status_code == 204
        # 确认已删
        list_resp = await client.get(_api(), headers=auth)
        ids = [d["id"] for d in list_resp.json()]
        assert dash_id not in ids

    @pytest.mark.asyncio
    async def test_delete_nonexistent_dashboard(self, client, auth):
        resp = await client.delete(_api("/no_such_id"), headers=auth)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, client, auth, auth_b):
        """租户 A 创建的看板, 租户 B 看不到。"""
        await client.post(_api(), json={"name": "A的看板"}, headers=auth)
        list_b = await client.get(_api(), headers=auth_b)
        names_b = [d["name"] for d in list_b.json()]
        assert "A的看板" not in names_b


class TestWidgetCRUD:
    """Widget CRUD (需要先创建看板 + 数据源)。"""

    @pytest.mark.asyncio
    async def test_add_widget_empty_question(self, client, auth):
        """空 question → 422 (Pydantic 验证)。"""
        create_resp = await client.post(_api(), json={"name": "看板"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.post(
            _api(f"/{dash_id}/widgets"),
            json={"question": "", "datasource_id": "ds1", "query_sql": "SELECT 1"},
            headers=auth,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_add_widget_no_datasource(self, client, auth):
        """空 datasource_id → 看板存在但数据源查不到, 图表配置生成跳过, widget 仍创建。"""
        create_resp = await client.post(_api(), json={"name": "看板"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.post(
            _api(f"/{dash_id}/widgets"),
            json={"question": "测试", "datasource_id": "", "query_sql": "SELECT 1"},
            headers=auth,
        )
        # datasource_id="" 在 DB 查询时找不到数据源 → 图表配置生成跳过, 但 widget 仍正常创建
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_add_widget_no_sql(self, client, auth):
        """空 query_sql → 看板存在, 图表配置生成时 validate_sql 拦截。"""
        create_resp = await client.post(_api(), json={"name": "看板"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.post(
            _api(f"/{dash_id}/widgets"),
            json={"question": "测试", "datasource_id": "ds1", "query_sql": ""},
            headers=auth,
        )
        # query_sql="" 通过 Pydantic 验证, 但 validate_sql() 在执行时拦截
        # 图表配置生成在 try/except 内, 失败不阻塞, widget 仍创建 (chart_config=None)
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_add_widget_nonexistent_dashboard(self, client, auth):
        """看板不存在 → 404。"""
        resp = await client.post(
            _api("/no_such_dash/widgets"),
            json={"question": "测试", "datasource_id": "ds1", "query_sql": "SELECT 1"},
            headers=auth,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_widget_nonexistent(self, client, auth):
        """删除不存在的 widget → 404。"""
        create_resp = await client.post(_api(), json={"name": "看板"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.delete(
            _api(f"/{dash_id}/widgets/no_such_widget"), headers=auth,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_layout_update_nonexistent_dashboard(self, client, auth):
        """看板不存在 → 404。"""
        resp = await client.put(
            _api("/no_such_dash/widgets/layout"),
            json=[{"id": "w1", "x": 1, "y": 2, "w": 3, "h": 4}],
            headers=auth,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_dashboard_detail(self, client, auth):
        """获取看板详情 (含空 widget 列表)。"""
        create_resp = await client.post(_api(), json={"name": "详情看板"}, headers=auth)
        dash_id = create_resp.json()["id"]
        resp = await client.get(_api(f"/{dash_id}"), headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "详情看板"
        assert data["widgets"] == []

    @pytest.mark.asyncio
    async def test_get_nonexistent_dashboard(self, client, auth):
        resp = await client.get(_api("/no_such_id"), headers=auth)
        assert resp.status_code == 404
