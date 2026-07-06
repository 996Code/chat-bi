"""
T038: 上下文压缩 — 单元测试

对标:
  - Claude Code §6: 压缩不是截断, 是"压缩 + 状态补偿"
  - CMP-001: token 超 70% 触发
  - CMP-002: 压缩后状态补偿 (语义层/SQL/filters)
  - CMP-004: 熔断器 (连续3次失败停止)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.compressor import (
    should_compress,
    compact_history,
    CompressionCircuitBreaker,
    estimate_tokens,
)


# ── Token 估算 ────────────────────────────────────────────────

class TestEstimateTokens:
    """粗估 token 数 (中文 ~1.5 字/token, 英文 ~4 字符/token)。"""

    def test_english_text(self):
        # ~4 chars per token
        tokens = estimate_tokens("a" * 400)
        assert 80 <= tokens <= 120

    def test_chinese_text(self):
        # 中文每个字约 1-2 token
        tokens = estimate_tokens("销售" * 100)  # 200 字
        assert tokens > 100

    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_mixed(self):
        tokens = estimate_tokens("本月销售额 SELECT * FROM orders")
        assert tokens > 5


# ── 触发判断 ──────────────────────────────────────────────────

class TestShouldCompress:
    """token 超 70% 模型上限 → 触发压缩 (CMP-001)。"""

    def test_below_threshold_no_compress(self):
        """token < 70% → 不压缩。"""
        assert not should_compress(messages=_mock_messages(100), model_limit=10000)

    def test_above_threshold_compress(self):
        """token > 70% → 触发。"""
        assert should_compress(messages=_mock_messages(8000), model_limit=10000)

    def test_empty_messages_no_compress(self):
        assert not should_compress(messages=[], model_limit=10000)


def _mock_messages(tokens_approx: int) -> list[dict]:
    """构造约 N tokens 的 messages。"""
    text = "x" * (tokens_approx * 4)  # ~4 chars/token
    return [{"role": "user", "content": text}]


# ── 压缩 ──────────────────────────────────────────────────────

class TestCompactHistory:
    """旧轮次 → 摘要 + 状态补偿 (CMP-002)。"""

    @pytest.mark.asyncio
    async def test_compact_keeps_recent_turns(self):
        """保留最近 3 轮完整对话 (CMP-002)。"""
        turns = []
        for i in range(5):
            turns.append({"role": "user", "content": f"第{i}轮问题"})
            turns.append({"role": "assistant", "content": f"第{i}轮回答"})
        mock_resp = MagicMock()
        mock_resp.usage = None
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=("用户查询了销售数据", mock_resp)):
            result = await compact_history(turns, keep_recent=3)

        # 保留最近 3 轮 (6 条 message)
        assert len(result.recent_messages) <= 6
        # 旧轮次有摘要
        assert result.summary is not None

    @pytest.mark.asyncio
    async def test_compact_returns_summary(self):
        """旧轮次压缩成一句话摘要。"""
        turns = [{"role": "user", "content": "本月销售额"}, {"role": "assistant", "content": "125000"}]
        mock_resp = MagicMock()
        mock_resp.usage = None
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=("用户查了本月销售额, 结果 125000", mock_resp)):
            result = await compact_history(turns, keep_recent=0)
        assert "销售" in result.summary or "125000" in result.summary

    @pytest.mark.asyncio
    async def test_compact_llm_failure_degrades(self):
        """LLM 压缩失败 → 降级 (简单截断 + warning, 不崩)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = await compact_history(
                [{"role": "user", "content": "x"}], keep_recent=0,
            )
        # 降级: 摘要为空或截断文本, 不抛
        assert result.error is not None or result.summary == ""

    @pytest.mark.asyncio
    async def test_compact_short_history_no_compress(self):
        """对话不够长 (≤ keep_recent) → 不压缩, 原样返回。"""
        turns = [{"role": "user", "content": "x"}]
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            result = await compact_history(turns, keep_recent=3)
        assert result.summary == ""  # 没压缩
        mock_chat.assert_not_called()


# ── 熔断器 ────────────────────────────────────────────────────

class TestCompressionCircuitBreaker:
    """连续 3 次压缩失败 → 熔断 (CMP-004, 对标 §6.4)。"""

    def test_below_threshold_not_tripped(self):
        cb = CompressionCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_tripped()

    def test_at_threshold_tripped(self):
        cb = CompressionCircuitBreaker(threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_tripped()

    def test_success_resets(self):
        cb = CompressionCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert not cb.is_tripped()
