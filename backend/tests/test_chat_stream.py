"""
ChatBI v2 — 流式问答 (chat_stream) 单元测试

对标 test_agent.py 的 run_agent 测试, 但测的是 chat_stream.py 的 event_stream
(SSE 流式管线), 验证流式接口的核心行为:
  - SQL 自愈循环: 执行失败 → heal_sql 修复 → 重执行 (对标 AEE-002)
  - SSE 事件序列正确 (sql/heal/data/complete)

测试方式: mock AgentDeps (不依赖真实 LLM/DB), 直接调 chat_stream 端点收集
StreamingResponse 的 SSE 事件流做断言。
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from app.ai.intent import IntentOutput
from app.api import chat_stream as cs
from app.api.chat import ChatRequest
from app.schemas.semantic_layer import Column, Model, SemanticModelContent


def _make_content() -> SemanticModelContent:
    """构造带列的语义层 (让 allowed_columns 非空, 否则管线在 schema 阶段终止)。"""
    return SemanticModelContent(models=[
        Model(name="biz_orders", display_name="订单", columns=[
            Column(name="id", display_name="ID", data_type="INT"),
            Column(name="total_amount", display_name="金额", data_type="DECIMAL"),
        ]),
    ])


def _mock_intent(intent: str = "TEXT_TO_SQL"):
    return IntentOutput(intent=intent, normalized_question="销售额", confidence=0.9, reason="")


def _exec_result(error: str | None, rows=None, columns=None):
    """构造 ExecuteResult mock。"""
    return MagicMock(
        error=error,
        rows=rows or [],
        columns=columns or [],
        truncated=False,
        duration_ms=10,
    )


async def _run_stream(deps, content) -> tuple[list[str], list[dict]]:
    """调 chat_stream 端点, 收集 SSE 事件流。

    Returns: (事件类型列表, 所有 data payload 列表)
    """
    user = MagicMock(tenant_id="t1", user_id="u1")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.commit = AsyncMock()

    with patch("app.api.chat.build_agent_deps", AsyncMock(return_value=(deps, content))), \
         patch("app.ai.state_store.StateStore") as MockStore, \
         patch("app.core.token_tracker.stop_token_tracking", return_value={}), \
         patch("app.core.token_tracker.start_token_tracking", return_value="tok"), \
         patch("app.core.prompt_capture.start_prompt_capture", return_value="pc"), \
         patch("app.core.prompt_capture.stop_prompt_capture", return_value=None), \
         patch("app.api.chat_stream.write_audit_log", AsyncMock()):
        MockStore.return_value.load.return_value = None
        MockStore.return_value.list_turns.return_value = []
        MockStore.return_value.save = MagicMock()

        body = ChatRequest(question="销售额", data_source_id="ds1")
        scope = {"type": "http", "headers": [], "method": "POST", "path": "/", "query_params": {}}
        resp = await cs.chat_stream(request=Request(scope), body=body, user=user, db=db)

        chunks = []
        async for c in resp.body_iterator:
            chunks.append(c.decode() if isinstance(c, bytes) else c)
        full = "".join(chunks)

    events = [line[7:] for line in full.split("\n") if line.startswith("event: ")]
    payloads = []
    for line in full.split("\n"):
        if line.startswith("data: "):
            try:
                payloads.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events, payloads


class TestStreamSelfHeal:
    """流式管线的 SQL 自愈循环 (对标 test_agent.TestFailureRouting)。"""

    async def test_heal_success_then_query_succeeds(self):
        """执行失败 → 自愈修复 → 重执行成功 → heal + data 事件 + self_heal_rounds=1。"""
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent())
        deps.retrieve = AsyncMock(return_value=MagicMock(
            models=[{"name": "biz_orders", "score": 0.8}], no_match_reason=None, degraded=False,
        ))
        deps.should_ask_for_schema = MagicMock(return_value=None)
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error=None, sql="SELECT total_amount FROM biz_orders",
            validation=MagicMock(ok=True), fewshot_count=0,
        ))
        # 第一次执行失败, 自愈后第二次成功
        deps.execute_sql = AsyncMock(side_effect=[
            _exec_result("Unknown column 'total_amount'"),
            _exec_result(None, rows=[(1,)], columns=["id"]),
        ])
        deps.heal_sql = AsyncMock(return_value=MagicMock(
            success=True, sql="SELECT id FROM biz_orders", validation=MagicMock(ok=True),
        ))
        deps.check_result = MagicMock(return_value=MagicMock(ok=True))
        deps.should_ask_for_result = MagicMock(return_value=None)
        deps.generate_chart = AsyncMock(return_value=MagicMock(option={"chart_type": "bar"}, degraded=False))
        deps.max_self_heal_rounds = 2

        events, payloads = await _run_stream(deps, _make_content())

        # 事件序列: 必须有 sql → heal → data → complete
        assert "sql" in events
        assert "heal" in events, "自愈未触发: heal 事件缺失"
        assert "data" in events, "自愈后未重发 data 事件"
        assert "complete" in events

        # complete 必须成功, 自愈 1 轮
        complete = next(p for p in payloads if "self_heal_rounds" in p and p.get("success") is not None and "conversation_id" in p)
        assert complete["success"] is True
        assert complete["self_heal_rounds"] == 1

        # heal 事件携带修复前后 SQL (OBS-003: 展示对比)
        heal = next(p for p in payloads if p.get("before_sql"))
        assert heal["success"] is True
        assert heal["before_sql"] == "SELECT total_amount FROM biz_orders"
        assert heal["sql"] == "SELECT id FROM biz_orders"

        deps.heal_sql.assert_called_once()

    async def test_heal_exhausted_max_rounds(self):
        """自愈达到最大轮数仍未解决 → 失败结束, 不无限循环。"""
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent())
        deps.retrieve = AsyncMock(return_value=MagicMock(
            models=[{"name": "biz_orders", "score": 0.8}], no_match_reason=None, degraded=False,
        ))
        deps.should_ask_for_schema = MagicMock(return_value=None)
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error=None, sql="SELECT bad FROM biz_orders",
            validation=MagicMock(ok=True), fewshot_count=0,
        ))
        # 每次执行都失败 (自愈修不好)
        deps.execute_sql = AsyncMock(return_value=_exec_result("syntax error"))
        deps.heal_sql = AsyncMock(return_value=MagicMock(
            success=True, sql="SELECT bad FROM biz_orders", validation=MagicMock(ok=True),
        ))
        deps.check_result = MagicMock(return_value=MagicMock(ok=True))
        deps.should_ask_for_result = MagicMock(return_value=None)
        deps.max_self_heal_rounds = 2  # 最多 2 轮, 防无限循环

        events, payloads = await _run_stream(deps, _make_content())

        assert "complete" in events
        complete = next(p for p in payloads if "self_heal_rounds" in p and p.get("success") is not None and "conversation_id" in p)
        # 自愈耗尽 → 失败
        assert complete["success"] is False
        assert complete["self_heal_rounds"] == 2, "必须恰好自愈 max_rounds 轮, 不能多也不能少"
        # heal 事件应该有 2 个 (每轮一个)
        heal_events = [e for e in events if e == "heal"]
        assert len(heal_events) == 2

    async def test_heal_sql_returns_failure(self):
        """heal_sql 本身失败 (LLM 无法修复) → 立即结束, 不再重执行。"""
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent())
        deps.retrieve = AsyncMock(return_value=MagicMock(
            models=[{"name": "biz_orders", "score": 0.8}], no_match_reason=None, degraded=False,
        ))
        deps.should_ask_for_schema = MagicMock(return_value=None)
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error=None, sql="SELECT bad FROM biz_orders",
            validation=MagicMock(ok=True), fewshot_count=0,
        ))
        deps.execute_sql = AsyncMock(return_value=_exec_result("column not found"))
        # heal_sql 直接返回失败
        deps.heal_sql = AsyncMock(return_value=MagicMock(
            success=False, error="无法修复", validation=MagicMock(ok=True),
        ))
        deps.check_result = MagicMock(return_value=MagicMock(ok=True))
        deps.should_ask_for_result = MagicMock(return_value=None)
        deps.max_self_heal_rounds = 2

        events, payloads = await _run_stream(deps, _make_content())

        complete = next(p for p in payloads if "self_heal_rounds" in p and p.get("success") is not None and "conversation_id" in p)
        assert complete["success"] is False
        assert complete["self_heal_rounds"] == 1, "heal_sql 失败应立即停止, 只自愈 1 轮"
        deps.heal_sql.assert_called_once()
