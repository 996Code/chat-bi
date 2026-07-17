"""
#1 慢 SQL 预警测试 (DSO-07):
  - ExecuteResult 记录 duration_ms
  - AuditLog 持久化 is_slow/duration_ms
  - GET /slow-queries 端点
"""
import pytest

from app.core.security import create_access_token
from app.db.models import AuditLog


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


# ── ExecuteResult 记录耗时 ─────────────────────────────────────

class TestExecuteDuration:
    """DSO-07: sql_executor 记录执行耗时。"""

    def test_execute_result_has_duration_field(self):
        """ExecuteResult 有 duration_ms 字段 (默认 0)。"""
        from app.services.sql_executor import ExecuteResult
        r = ExecuteResult(rows=[], columns=["a"])
        assert r.duration_ms == 0

    def test_slow_query_threshold_config(self):
        """config.sql_slow_query_threshold 可配置 (默认 10s)。"""
        from app.core.config import get_settings
        s = get_settings()
        assert s.sql_slow_query_threshold == 10.0
        assert isinstance(s.sql_slow_query_threshold, float)


# ── AuditLog 慢查询字段 + 端点 ─────────────────────────────────

class TestSlowQueryAudit:
    """DSO-07: AuditLog 记录 is_slow/duration_ms + /slow-queries 端点。"""

    async def test_auditlog_has_slow_fields(self, db_session):
        """AuditLog 模型含 duration_ms / is_slow 字段。"""
        log = AuditLog(
            tenant_id="tenant_A", user_id="admin_1",
            resource_type="chat", action="query", status="success",
            sql_text="SELECT * FROM big_table", duration_ms=15000, is_slow=True,
        )
        db_session.add(log)
        await db_session.commit()
        await db_session.refresh(log)
        assert log.duration_ms == 15000
        assert log.is_slow is True

    async def test_slow_queries_endpoint(self, http_client, admin_token, db_session):
        """GET /slow-queries 返回 is_slow=True 的记录 (admin only)。"""
        # 插入 2 条: 1 慢 1 快
        db_session.add(AuditLog(
            tenant_id="tenant_A", user_id="admin_1",
            resource_type="chat", action="query", status="success",
            sql_text="SELECT * FROM huge", duration_ms=25000, is_slow=True,
        ))
        db_session.add(AuditLog(
            tenant_id="tenant_A", user_id="admin_1",
            resource_type="chat", action="query", status="success",
            sql_text="SELECT 1", duration_ms=5, is_slow=False,
        ))
        await db_session.commit()

        res = await http_client.get("/chat-bi/api/v1/slow-queries", headers=_auth(admin_token))
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1  # 只有慢的那条
        assert data[0]["is_slow"] is True
        assert data[0]["duration_ms"] == 25000

    async def test_slow_queries_user_forbidden(self, http_client, user_token):
        """非 admin 访问 /slow-queries → 403 (fail-closed)。"""
        res = await http_client.get("/chat-bi/api/v1/slow-queries", headers=_auth(user_token))
        assert res.status_code == 403

    async def test_audit_logs_include_duration(self, http_client, admin_token, db_session):
        """GET /audit-logs 返回里含 duration_ms / is_slow 字段。"""
        db_session.add(AuditLog(
            tenant_id="tenant_A", user_id="admin_1",
            resource_type="chat", action="query", status="success",
            sql_text="SELECT 1", duration_ms=50, is_slow=False,
        ))
        await db_session.commit()
        res = await http_client.get("/chat-bi/api/v1/audit-logs", headers=_auth(admin_token))
        assert res.status_code == 200
        item = res.json()[0]
        assert "duration_ms" in item
        assert "is_slow" in item
        assert item["is_slow"] is False

    async def test_write_audit_log_accepts_slow_params(self, db_session):
        """write_audit_log 接受 duration_ms/is_slow 参数 (回归防护: P0 曾因签名缺参数崩溃)。

        走真实 write_audit_log 调用链, 确保审计写入不抛 TypeError。
        """
        from app.core.auth import write_audit_log
        # 需要一个真实 tenant (AuditLog.tenant_id 有外键约束)
        from app.db.models import Tenant
        db_session.add(Tenant(id="tenant_audit", name="test"))
        await db_session.flush()

        # 传 duration_ms + is_slow, 不应抛 TypeError
        await write_audit_log(
            db_session, tenant_id="tenant_audit", user_id=None,
            resource_type="chat", action="query", status="success",
            sql_text="SELECT 1", duration_ms=15000, is_slow=True,
        )
        await db_session.commit()

        # 验证入库
        result = await db_session.execute(
            AuditLog.__table__.select().where(AuditLog.is_slow == True)  # noqa: E712
        )
        rows = result.fetchall()
        assert len(rows) == 1
        assert rows[0].duration_ms == 15000

    async def test_write_audit_log_without_slow_params_backward_compat(self, db_session):
        """旧调用点(不传 duration_ms/is_slow)仍正常 (向后兼容)。"""
        from app.core.auth import write_audit_log
        from app.db.models import Tenant
        db_session.add(Tenant(id="tenant_bwd", name="test"))
        await db_session.flush()

        await write_audit_log(
            db_session, tenant_id="tenant_bwd", user_id=None,
            resource_type="data_source", action="create", status="success",
        )
        await db_session.commit()
        # 不抛异常即通过 (is_slow 默认 False)
