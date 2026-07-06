"""
T026: 意图识别 — 单元测试

对标:
  - INT-001~004 (openspec spec): 5意图 + Pydantic + confidence<0.6降级 + normalized_question剥离可视化
  - 海泰: 追问维度继承 (本任务只做 normalized_question, 维度继承留 T029 State)

5 意图:
  TEXT_TO_SQL  "本月销售额"      → 完整 SQL 生成管道
  CLARIFICATION "那个呢"         → 需要上下文补全或追问
  GENERAL      "你好"            → 闲聊, 不碰 DB
  CHART_MODIFY "换成饼图"        → 只改图表, 不改 SQL
  EXPLANATION  "这个SQL什么意思" → 解释已有 SQL/结果
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.ai.intent import (
    IntentOutput,
    classify_intent,
    strip_visualization,
)


# ── IntentOutput Pydantic 约束 ────────────────────────────────

class TestIntentOutput:
    """Pydantic 强约束 (对标 INT-002)。"""

    def test_valid_intent(self):
        out = IntentOutput(
            intent="TEXT_TO_SQL",
            normalized_question="本月销售额",
            confidence=0.9,
            reason="查询销售额数据",
        )
        assert out.intent == "TEXT_TO_SQL"

    def test_invalid_intent_rejected(self):
        """非 5 种意图 → 拒绝。"""
        with pytest.raises(ValidationError):
            IntentOutput(
                intent="UNKNOWN",
                normalized_question="x",
                confidence=0.5,
                reason="",
            )

    def test_confidence_range(self):
        """confidence 必须在 [0, 1]。"""
        with pytest.raises(ValidationError):
            IntentOutput(intent="GENERAL", normalized_question="x", confidence=1.5, reason="")
        with pytest.raises(ValidationError):
            IntentOutput(intent="GENERAL", normalized_question="x", confidence=-0.1, reason="")

    def test_chart_type_hint_optional(self):
        out = IntentOutput(
            intent="TEXT_TO_SQL",
            normalized_question="销售额",
            confidence=0.9,
            reason="",
            chart_type_hint="bar",
        )
        assert out.chart_type_hint == "bar"


# ── strip_visualization (剥离可视化措辞) ──────────────────────

class TestStripVisualization:
    """对标 INT-004: 从 normalized_question 剥离"用折线图展示"等可视化措辞。"""

    def test_strip_line_chart(self):
        assert strip_visualization("用折线图展示本月销售额") == "本月销售额"

    def test_strip_pie_chart(self):
        assert strip_visualization("画成饼图 各品类占比") == "各品类占比"

    def test_strip_bar_chart(self):
        q, hint = strip_visualization("本月销售柱状图", return_hint=True)
        assert "销售" in q
        assert hint is not None

    def test_no_visualization_unchanged(self):
        assert strip_visualization("本月销售额") == "本月销售额"

    def test_hint_extracted(self):
        """剥离时提取 chart_type_hint。"""
        q, hint = strip_visualization("用折线图展示本月销售额", return_hint=True)
        assert q == "本月销售额"
        assert hint == "line"


# ── classify_intent ───────────────────────────────────────────

def _mock_llm_content(intent: str, confidence: float = 0.9, question: str = "x", chart_hint=None):
    """返回 (content, mock_resp) 元组, 用于 patch llm_chat 的 return_value。"""
    payload = {
        "intent": intent,
        "normalized_question": question,
        "confidence": confidence,
        "reason": "test",
        "chart_type_hint": chart_hint,
    }
    mock_resp = MagicMock()
    mock_resp.usage = None
    return (json.dumps(payload), mock_resp)


class TestClassifyIntent:
    """意图分类: LLM + confidence 降级。"""

    @pytest.mark.asyncio
    async def test_text_to_sql(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("TEXT_TO_SQL", 0.9, "本月销售额")):
            result = await classify_intent("本月销售额", None)
        assert result.intent == "TEXT_TO_SQL"

    @pytest.mark.asyncio
    async def test_general(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("GENERAL", 0.95, "你好")):
            result = await classify_intent("你好", None)
        assert result.intent == "GENERAL"

    @pytest.mark.asyncio
    async def test_chart_modify(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("CHART_MODIFY", 0.85, "换成饼图", chart_hint="pie")):
            result = await classify_intent("换成饼图", None)
        assert result.intent == "CHART_MODIFY"
        assert result.chart_type_hint == "pie"

    @pytest.mark.asyncio
    async def test_explanation(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("EXPLANATION", 0.8, "这个SQL什么意思")):
            result = await classify_intent("解释一下", None)
        assert result.intent == "EXPLANATION"

    @pytest.mark.asyncio
    async def test_low_confidence_degrades_to_clarification(self):
        """confidence < 0.6 → 降级 CLARIFICATION (对标 INT-002)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("TEXT_TO_SQL", 0.4, "那个呢")):
            result = await classify_intent("那个呢", None)
        assert result.intent == "CLARIFICATION"
        assert "降级" in result.reason or "CLARIFICATION" in result.reason

    @pytest.mark.asyncio
    async def test_visualization_stripped_from_question(self):
        """normalized_question 应剥离可视化措辞 (对标 INT-004)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("TEXT_TO_SQL", 0.9, "本月销售额", chart_hint="line")):
            result = await classify_intent("用折线图展示本月销售额", None)
        # LLM 返回的 normalized_question 应已剥离 (LLM 负责), 这里验证 hint 传递
        assert result.chart_type_hint == "line"

    @pytest.mark.asyncio
    async def test_invalid_json_degrades(self):
        """LLM 返回非法 JSON → 降级 CLARIFICATION (宁缺毋滥)。"""
        mock_resp = MagicMock()
        mock_resp.usage = None
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=("这不是JSON{", mock_resp)):
            result = await classify_intent("问题", None)
        assert result.intent == "CLARIFICATION"

    @pytest.mark.asyncio
    async def test_schema_mismatch_retries(self):
        """LLM 输出不符合 schema (非5种意图) → 重试 (对标 INT-002)。"""
        call_count = 0
        async def chat_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                content = json.dumps({"intent": "BAD", "normalized_question": "x", "confidence": 0.9})
            else:
                content = json.dumps({"intent": "GENERAL", "normalized_question": "你好", "confidence": 0.9, "reason": ""})
            mock_resp = MagicMock()
            mock_resp.usage = None
            return (content, mock_resp)

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=chat_side_effect):
            result = await classify_intent("你好", None)
        assert result.intent == "GENERAL"
        assert call_count >= 2  # 重试过

    @pytest.mark.asyncio
    async def test_retry_exhausted_degrades(self):
        """重试 max 次仍失败 → CLARIFICATION。"""
        mock_resp = MagicMock()
        mock_resp.usage = None
        bad_content = json.dumps({"intent": "BAD", "normalized_question": "x", "confidence": 0.9})
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=(bad_content, mock_resp)):
            result = await classify_intent("问题", None)
        assert result.intent == "CLARIFICATION"

    @pytest.mark.asyncio
    async def test_llm_failure_degrades(self):
        """LLM 调用失败 → CLARIFICATION (降级, 不抛)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = await classify_intent("问题", None)
        assert result.intent == "CLARIFICATION"
