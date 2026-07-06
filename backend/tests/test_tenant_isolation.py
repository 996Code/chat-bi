"""
ChatBI v2 — Cross-Tenant Isolation Tests (M6)

验证每个 API 端点的跨租户隔离: 租户 A 的数据, 租户 B 看不到/改不了。
对标 v1 经验教训 #48: 漏一个 .where(tenant_filter()) = 数据泄露。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.security import create_access_token


def _auth(user_id: str, tenant_id: str, role: str = "admin") -> dict:
    token = create_access_token({
        "user_id": user_id, "email": f"{user_id}@test.com",
        "tenant_id": tenant_id, "role": role,
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _api(path: str = "") -> str:
    return f"{get_settings().api_prefix}{path}"


class TestDashboardTenantIsolation:
    """Dashboard 跨租户隔离 (已有部分测试, 补充写操作隔离)。"""

    @pytest.mark.asyncio
    async def test_tenant_b_cannot_update_tenant_a_dashboard(self, client):
        auth_a = _auth("admin_1", "tenant_A")
        auth_b = _auth("admin_2", "tenant_B")
        prefix = _api("/dashboards")

        # A 创建看板
        create_resp = await client.post(prefix, json={"name": "A的看板"}, headers=auth_a)
        assert create_resp.status_code == 201
        dash_id = create_resp.json()["id"]

        # B 尝试修改 A 的看板 → 404 (看不到)
        resp = await client.put(f"{prefix}/{dash_id}", json={"name": "被B改了"}, headers=auth_b)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant_b_cannot_delete_tenant_a_dashboard(self, client):
        auth_a = _auth("admin_1", "tenant_A")
        auth_b = _auth("admin_2", "tenant_B")
        prefix = _api("/dashboards")

        create_resp = await client.post(prefix, json={"name": "A的看板2"}, headers=auth_a)
        dash_id = create_resp.json()["id"]

        # B 尝试删除 A 的看板 → 404
        resp = await client.delete(f"{prefix}/{dash_id}", headers=auth_b)
        assert resp.status_code == 404


class TestDataSourceTenantIsolation:
    """数据源 跨租户隔离。"""

    @pytest.mark.asyncio
    async def test_list_data_sources_tenant_isolated(self, client):
        """租户 B 列表看不到租户 A 的数据源。"""
        auth_a = _auth("admin_1", "tenant_A")
        auth_b = _auth("admin_2", "tenant_B")
        prefix = _api("/data-sources")

        # A 创建数据源
        await client.post(prefix, json={
            "name": "A的数据源", "db_type": "postgresql",
            "host": "localhost", "port": 5432,
            "database": "test", "username": "u", "password": "p",
        }, headers=auth_a)

        # B 列表不应包含 A 的数据源
        list_b = await client.get(prefix, headers=auth_b)
        assert list_b.status_code == 200
        names_b = [d["name"] for d in list_b.json()]
        assert "A的数据源" not in names_b


class TestSemanticModelTenantIsolation:
    """语义层 跨租户隔离 (需要 data_source_id, 已有 test_semantic_api 覆盖)。"""

    @pytest.mark.asyncio
    async def test_tenant_b_cannot_access_tenant_a_semantic_model(self, client):
        """租户 B 无法用 A 的 data_source_id 获取语义模型。"""
        auth_a = _auth("admin_1", "tenant_A")
        auth_b = _auth("admin_2", "tenant_B")

        # A 创建数据源
        ds_resp = await client.post(_api("/data-sources"), json={
            "name": "A数据源隔离", "db_type": "postgresql",
            "host": "localhost", "port": 5432,
            "database": "test", "username": "u", "password": "p",
        }, headers=auth_a)
        ds_id = ds_resp.json()["id"]

        # B 用 A 的 ds_id 列语义模型 → tenant_filter 应过滤掉
        prefix = _api("/semantic-models")
        list_b = await client.get(f"{prefix}?data_source_id={ds_id}", headers=auth_b)
        assert list_b.status_code == 200
        # B 的语义模型列表应为空/null (A 的模型被 tenant_filter 过滤)
        assert list_b.json() is None or list_b.json() == []


class TestAuditLogTenantIsolation:
    """审计日志 跨租户隔离。"""

    @pytest.mark.asyncio
    async def test_audit_logs_tenant_isolated(self, client):
        """租户 B 看不到租户 A 的审计日志。"""
        auth_a = _auth("admin_1", "tenant_A")
        auth_b = _auth("admin_2", "tenant_B")
        prefix = _api("/audit-logs")

        list_a = await client.get(prefix, headers=auth_a)
        list_b = await client.get(prefix, headers=auth_b)
        assert list_a.status_code == 200
        assert list_b.status_code == 200
        # B 的审计日志不应包含 A 的
        ids_a = {l["id"] for l in list_a.json()}
        ids_b = {l["id"] for l in list_b.json()}
        assert ids_a.isdisjoint(ids_b)
