"""
Wave 1 后端能力测试:
  - T049: token_tracker 全节点累加
  - T050: prompt_capture 请求级捕获 + trace 端点
  - T051: saved_queries 端点 (列表/详情/多租户隔离)
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.core.token_tracker import (
    start_token_tracking, stop_token_tracking, track_usage, TokenTracker,
)
from app.core.prompt_capture import (
    start_prompt_capture, stop_prompt_capture, record_prompt, PromptCapture,
)
from app.db.models import SavedQuery


# ── fixtures ──────────────────────────────────────────────────

@pytest.fixture
def admin_token():
    return create_access_token({
        "user_id": "admin_1", "email": "admin@test.com",
        "tenant_id": "tenant_A", "role": "admin",
    })


@pytest.fixture
def other_tenant_token():
    return create_access_token({
        "user_id": "admin_2", "email": "admin2@test.com",
        "tenant_id": "tenant_B", "role": "admin",
    })


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── T049: token_tracker 全节点累加 ─────────────────────────────

class TestTokenTracker:
    """T049: 多节点 track_usage 累加正确 (对标 OBS-002)。"""

    def test_accumulate_across_nodes(self):
        """多个节点调 track_usage, 累加到同一请求统计。"""
        tt = start_token_tracking()
        try:
            # 模拟 3 个节点的 usage (类 OpenAI CompletionUsage)
            class _U:
                def __init__(self, p, c):
                    self.prompt_tokens = p
                    self.completion_tokens = c
                    self.total_tokens = p + c

            track_usage(_U(100, 20))   # intent
            track_usage(_U(500, 50))   # generate_sql
            track_usage(_U(200, 10))   # generate_chart
        finally:
            stats = stop_token_tracking(tt)

        assert stats["prompt_tokens"] == 800
        assert stats["completion_tokens"] == 80
        assert stats["total_tokens"] == 880
        assert stats["llm_calls"] == 3

    def test_track_usage_no_context_silent(self):
        """无追踪上下文时 track_usage 静默跳过 (不影响扫描/标题生成等非 chat 场景)。"""
        # 不调 start, 直接 track 不应抛异常
        track_usage(None)
        track_usage(type("U", (), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})())
        # 无异常即通过

    def test_stop_returns_empty_when_no_tracker(self):
        """无 tracker 时 stop 返回空 dict (健壮性)。"""
        tt = start_token_tracking()
        stats = stop_token_tracking(tt)
        assert stats == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "llm_calls": 0}


# ── T050: prompt_capture 请求级捕获 ────────────────────────────

class TestPromptCapture:
    """T050: dump-prompts 的请求级 prompt 捕获。"""

    def test_capture_across_nodes(self):
        """多节点 record_prompt 累积到同一请求记录。"""
        pt = start_prompt_capture()
        try:
            class _U:
                def __init__(self, p, c):
                    self.prompt_tokens = p
                    self.completion_tokens = c
                    self.total_tokens = p + c

            record_prompt("intent", "sys-intent", "user-q", _U(100, 20))
            record_prompt("generate_sql", "sys-sql", "user-sql", _U(500, 50))
        finally:
            cap = stop_prompt_capture(pt)

        records = cap["records"]
        assert len(records) == 2
        assert records[0]["node"] == "intent"
        assert records[0]["system"] == "sys-intent"
        assert records[0]["prompt_tokens"] == 100
        assert records[1]["node"] == "generate_sql"
        assert records[1]["completion_tokens"] == 50

    def test_record_no_context_silent(self):
        """无捕获上下文 record_prompt 静默跳过。"""
        record_prompt("x", "s", "u", None)  # 无异常即通过

    def test_record_none_usage(self):
        """usage 为 None 时 token 记 0 (不报错)。"""
        pt = start_prompt_capture()
        try:
            record_prompt("think", "s", "u", None)
        finally:
            cap = stop_prompt_capture(pt)
        assert cap["records"][0]["prompt_tokens"] == 0

    def test_stop_empty_when_no_capture(self):
        """无 capture 时 stop 返回空 dict。"""
        pt = start_prompt_capture()
        cap = stop_prompt_capture(pt)
        assert cap == {"records": []}


# ── T051: saved_queries 端点 ────────────────────────────────────

class TestSavedQueriesAPI:
    """T051: 已保存查询列表/详情 + 多租户隔离。"""

    async def _create_saved_query(self, db_session, tenant_id="tenant_A", user_id="admin_1"):
        # PG 强制 conversation_id FK → 先建真实 Conversation 行, 用其 id (不再写死 "conv1")
        from app.db.models import Conversation
        conv = Conversation(tenant_id=tenant_id, user_id=user_id, title="测试会话")
        db_session.add(conv)
        await db_session.flush()
        sq = SavedQuery(
            tenant_id=tenant_id, user_id=user_id,
            conversation_id=conv.id,
            question="本月销售额", sql_text="SELECT SUM(amount) FROM orders",
            result_summary='{"row_count": 1}',
            chart_config={"series": [{"type": "bar"}]},
        )
        db_session.add(sq)
        await db_session.commit()
        await db_session.refresh(sq)
        return sq

    async def test_list_saved_queries(self, http_client, admin_token, db_session):
        """admin 列出已保存查询。"""
        await self._create_saved_query(db_session)
        res = await http_client.get("/chat-bi/api/v1/saved-queries", headers=_auth(admin_token))
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["question"] == "本月销售额"
        assert data[0]["chart_config"]["series"][0]["type"] == "bar"

    async def test_get_saved_query_detail(self, http_client, admin_token, db_session):
        """详情含 chart_config。"""
        sq = await self._create_saved_query(db_session)
        res = await http_client.get(
            f"/chat-bi/api/v1/saved-queries/{sq.id}", headers=_auth(admin_token),
        )
        assert res.status_code == 200
        assert res.json()["sql_text"] == "SELECT SUM(amount) FROM orders"

    async def test_tenant_isolation(self, http_client, other_tenant_token, db_session):
        """多租户隔离: tenant_B 看不到 tenant_A 的查询。"""
        await self._create_saved_query(db_session, tenant_id="tenant_A")
        res = await http_client.get(
            "/chat-bi/api/v1/saved-queries", headers=_auth(other_tenant_token),
        )
        assert res.status_code == 200
        assert res.json() == []  # tenant_B 看不到 tenant_A 数据

    async def test_get_nonexistent_404(self, http_client, admin_token):
        """查不存在的记录 → 404 (fail-closed)。"""
        res = await http_client.get(
            "/chat-bi/api/v1/saved-queries/nonexistent", headers=_auth(admin_token),
        )
        assert res.status_code == 404


# ── #5 CSV 导出端点校验 (UX-08) ────────────────────────────────

class TestCsvExport:
    """UX-08: 导出端点校验逻辑 (重跑需真实库, 这里测校验/权限/404)。"""

    async def test_export_nonexistent_query_404(self, http_client, admin_token):
        """导出不存在的 SavedQuery → 404。"""
        res = await http_client.get(
            "/chat-bi/api/v1/saved-queries/nonexistent/export",
            params={"data_source_id": "ds1"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 404

    async def test_export_missing_datasource_400(self, http_client, admin_token, db_session):
        """导出不传 data_source_id 且记录也无 → 400。"""
        from app.db.models import SavedQuery
        sq = SavedQuery(
            tenant_id="tenant_A", user_id="admin_1",
            question="q", sql_text="SELECT 1",
        )
        db_session.add(sq)
        await db_session.commit()
        await db_session.refresh(sq)
        # 不传 data_source_id 且记录无此字段 → 400 (业务层拒绝)
        res = await http_client.get(
            f"/chat-bi/api/v1/saved-queries/{sq.id}/export",
            headers=_auth(admin_token),
        )
        assert res.status_code == 400

    async def test_export_nonexistent_datasource_404(self, http_client, admin_token, db_session):
        """导出但数据源不存在 → 404。"""
        from app.db.models import SavedQuery
        sq = SavedQuery(
            tenant_id="tenant_A", user_id="admin_1",
            question="q", sql_text="SELECT 1",
        )
        db_session.add(sq)
        await db_session.commit()
        await db_session.refresh(sq)
        res = await http_client.get(
            f"/chat-bi/api/v1/saved-queries/{sq.id}/export",
            params={"data_source_id": "no_such_ds"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 404


# ── CSV 注入防护 (OWASP) ──────────────────────────────────────

class TestCsvSanitize:
    """CSV 导出注入防护 (= + - @ 开头加前缀)。"""

    def test_formula_prefix_escaped(self):
        from app.api.saved_queries import _sanitize_csv_cell
        # 公式注入字符加前缀 (OWASP: = + - @ 开头)
        assert _sanitize_csv_cell("=cmd") == "'=cmd"
        assert _sanitize_csv_cell("+1+1") == "'+1+1"
        assert _sanitize_csv_cell("-1-1") == "'-1-1"
        assert _sanitize_csv_cell("@risk") == "'@risk"
        # 普通值不变
        assert _sanitize_csv_cell("正常文本") == "正常文本"
        assert _sanitize_csv_cell("123") == "123"
        assert _sanitize_csv_cell(None) == ""
