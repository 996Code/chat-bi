"""
V2 新增功能测试:
  - DSO-04: semantic_diff 纯函数 (表级/列级 diff)
  - DSO-05: 数据源监控聚合端点
  - OPS-02: 备份恢复端点鉴权 + fail-closed
  - DSO-02: 数据源健康检查 ping 逻辑 (mock)
"""
import pytest

from app.core.security import create_access_token


@pytest.fixture
def admin_token():
    return create_access_token({
        "user_id": "admin_1", "email": "admin@test.com",
        "tenant_id": "tenant_A", "role": "admin",
    })


@pytest.fixture
def user_token():
    return create_access_token({
        "user_id": "user_1", "email": "user@test.com",
        "tenant_id": "tenant_A", "role": "user",
    })


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── DSO-04: semantic_diff 纯函数 ──────────────────────────────

class TestSemanticDiff:
    """DSO-04: 语义层 diff (表级 + 列级)。"""

    def test_identical_contents_no_diff(self):
        from app.services.semantic_diff import diff_semantic_contents
        content = {"models": [{"name": "users", "columns": [{"name": "id", "data_type": "int"}]}]}
        result = diff_semantic_contents(content, content)
        assert result["has_changes"] is False
        assert result["added_models"] == []
        assert result["changed_models"] == []

    def test_added_removed_table(self):
        from app.services.semantic_diff import diff_semantic_contents
        old = {"models": [{"name": "users", "columns": []}]}
        new = {"models": [{"name": "orders", "columns": []}]}
        result = diff_semantic_contents(old, new)
        assert result["added_models"] == ["orders"]
        assert result["removed_models"] == ["users"]

    def test_column_type_change_detected(self):
        """列类型变更 (data_type) 被检测。"""
        from app.services.semantic_diff import diff_semantic_contents
        old = {"models": [{"name": "users", "columns": [{"name": "age", "data_type": "int"}]}]}
        new = {"models": [{"name": "users", "columns": [{"name": "age", "data_type": "bigint"}]}]}
        result = diff_semantic_contents(old, new)
        assert result["has_changes"] is True
        assert len(result["changed_models"]) == 1
        assert result["changed_models"][0]["table"] == "users"
        assert "age" in result["changed_models"][0]["changed_columns"]

    def test_column_added_detected(self):
        from app.services.semantic_diff import diff_semantic_contents
        old = {"models": [{"name": "users", "columns": [{"name": "id"}]}]}
        new = {"models": [{"name": "users", "columns": [{"name": "id"}, {"name": "email"}]}]}
        result = diff_semantic_contents(old, new)
        assert "email" in result["changed_models"][0]["added_columns"]

    def test_display_name_only_change_is_top_changed(self):
        """display_name 变更 (但列没变) 仍算 changed。"""
        from app.services.semantic_diff import diff_semantic_contents
        old = {"models": [{"name": "u", "display_name": "旧", "columns": [{"name": "id", "data_type": "int"}]}]}
        new = {"models": [{"name": "u", "display_name": "新", "columns": [{"name": "id", "data_type": "int"}]}]}
        result = diff_semantic_contents(old, new)
        assert result["has_changes"] is True


# ── DSO-05: 数据源监控聚合 ────────────────────────────────────

class TestDatasourceMetrics:
    """DSO-05: GET /datasource-metrics 聚合统计。"""

    async def test_metrics_empty_when_no_audit(self, http_client, admin_token):
        """无审计记录 → 空列表。"""
        res = await http_client.get("/chat-bi/api/v1/datasource-metrics", headers=_auth(admin_token))
        assert res.status_code == 200
        assert res.json() == []

    async def test_metrics_aggregation(self, http_client, admin_token, db_session):
        """聚合统计正确 (query_count/avg_duration/error_rate)。"""
        from app.db.models import AuditLog
        # tenant_A + admin_1 由 conftest _seed_base_tenants 预置 (不再重复 add, 避免撞 pkey)

        ds_id = "ds_metrics_test"
        # 3 条: 2 成功 (100ms, 200ms) + 1 失败; 1 慢查询
        for dur, stat, slow in [(100, "success", False), (200, "success", False), (15000, "fail", True)]:
            db_session.add(AuditLog(
                tenant_id="tenant_A", user_id="admin_1",
                resource_type="chat", action="query", status=stat,
                data_source_id=ds_id, duration_ms=dur, is_slow=slow,
            ))
        await db_session.commit()

        res = await http_client.get("/chat-bi/api/v1/datasource-metrics", headers=_auth(admin_token))
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        m = data[0]
        assert m["query_count"] == 3
        assert m["avg_duration_ms"] == 5100  # (100+200+15000)/3
        assert m["error_rate"] == 33.3  # 1/3
        assert m["slow_count"] == 1

    async def test_metrics_user_forbidden(self, http_client, user_token):
        """非 admin → 403。"""
        res = await http_client.get("/chat-bi/api/v1/datasource-metrics", headers=_auth(user_token))
        assert res.status_code == 403
