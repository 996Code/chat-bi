"""
T027/T028: 预思考 + ask_user — 单元测试
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.thinking import think, ThinkingResult
from app.ai.ask_user import (
    should_ask_for_schema,
    should_ask_for_result,
    AskUserReason,
)


# ── T027 预思考 ───────────────────────────────────────────────

class TestThinking:
    @pytest.mark.asyncio
    async def test_think_returns_structured(self):
        mock_resp = MagicMock()
        mock_resp.usage = None
        llm_content = json.dumps({
            "tables": ["biz_orders (含金额)"],
            "aggregation": "SUM(total_amount) GROUP BY category",
            "caveats": ["注意 Fan-Trap: 多订单可能重复计算"],
        })
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=(llm_content, mock_resp)):
            result = await think("销售额", "orders(total_amount)", [{"name": "biz_orders", "score": 0.8}], None)
        assert result.error is None
        assert len(result.tables) == 1
        assert "SUM" in result.aggregation
        assert len(result.caveats) >= 1

    @pytest.mark.asyncio
    async def test_think_llm_failure_degrades(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = await think("x", "", [], None)
        assert result.error is not None
        assert result.tables == []  # 降级空, 不阻塞

    @pytest.mark.asyncio
    async def test_think_invalid_json_degrades(self):
        mock_resp = MagicMock()
        mock_resp.usage = None
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=("不是JSON", mock_resp)):
            result = await think("x", "", [], None)
        assert result.error is not None


# ── T028 ask_user ─────────────────────────────────────────────

class TestAskUser:
    def test_no_recall_asks_for_clarification(self):
        """检索无召回 → 问用户换问法。"""
        retrieval = MagicMock()
        retrieval.no_match_reason = "无法匹配到相关表"
        retrieval.models = []
        req = should_ask_for_schema(retrieval)
        assert req is not None
        assert req.reason == AskUserReason.SCHEMA_AMBIGUOUS

    def test_multiple_low_score_candidates_asks(self):
        """多候选且低分 → 问用户选哪个表。"""
        retrieval = MagicMock()
        retrieval.no_match_reason = None
        retrieval.models = [
            {"name": f"t{i}", "score": 0.4} for i in range(5)
        ]
        req = should_ask_for_schema(retrieval)
        assert req is not None
        assert req.options is not None

    def test_clear_match_no_ask(self):
        """高置信召回 → 不问 (正常继续)。"""
        retrieval = MagicMock()
        retrieval.no_match_reason = None
        retrieval.models = [{"name": "biz_orders", "score": 0.9}]
        assert should_ask_for_schema(retrieval) is None

    def test_normal_result_no_ask(self):
        """结果正常 → 不问。"""
        check = MagicMock(ok=True)
        assert should_ask_for_result(check) is None

    def test_abnormal_result_asks(self):
        """结果异常 → 问用户。"""
        check = MagicMock(ok=False, reason="0行", suggestion="检查条件")
        req = should_ask_for_result(check)
        assert req is not None
        assert req.reason == AskUserReason.RESULT_AMBIGUOUS
