"""
T024: Few-shot 历史匹配 — 单元测试

对标:
  - RAG-004 (openspec spec): 审核过的 Question-SQL Pair 作 few-shot, 最多 3 条
  - Claude Code: Relevant Recall (按需召回, 不全量灌入)

设计要点:
  - find_fewshot_examples(question, store, embedder, top_k=3):
    embed 问题 → 检索相似历史 SQL → 返回最多 3 条
  - fewshot 独立 collection (和 schema 索引分开, 避免混淆)
  - score 阈值: 低于阈值的不返回 (宁缺毋滥, 不返回弱相关示例)
  - 失败降级: 返回空列表 (不抛, Agent 无 fewshot 也能生成)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_store import SearchResult, VectorRecord
from app.services.fewshot import find_fewshot_examples, FewShotExample


def _make_fewshot_result(question: str, sql: str, score: float) -> SearchResult:
    return SearchResult(
        record=VectorRecord(
            id="fq1",
            vector=[0.1] * 1024,
            metadata={"question": question, "sql": sql, "type": "fewshot"},
            text=question,
        ),
        score=score,
    )


class TestFindFewShot:
    """few-shot 检索: 相似历史 SQL。"""

    @pytest.mark.asyncio
    async def test_returns_similar_examples(self):
        """召回相似历史 SQL, 按 score 降序。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_fewshot_result("本月销售额", "SELECT SUM(amount) FROM orders", 0.92),
            _make_fewshot_result("上月营收", "SELECT SUM(amount) FROM orders WHERE...", 0.88),
        ]
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("这个月的销售额", store, emb)

        assert len(examples) == 2
        assert all(isinstance(e, FewShotExample) for e in examples)
        assert examples[0].score >= examples[1].score  # 降序

    @pytest.mark.asyncio
    async def test_max_three_examples(self):
        """最多返回 3 条 (对标 RAG-004, 防 prompt token 爆炸)。

        真实 VectorStore 按 top_k 截断, mock 模拟该行为。
        """
        store = AsyncMock()
        all_results = [_make_fewshot_result(f"q{i}", "SELECT 1", 0.9) for i in range(10)]

        async def mock_search(vec, top_k, score_threshold, filter=None):
            return all_results[:top_k]  # 模拟 top_k 截断
        store.search.side_effect = mock_search
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("问题", store, emb, top_k=3)
        assert len(examples) == 3

    @pytest.mark.asyncio
    async def test_below_threshold_filtered(self):
        """score < 阈值 → 不返回 (宁缺毋滥, 弱相关示例会误导)。

        真实 VectorStore 按 score_threshold 过滤, mock 模拟该行为。
        """
        high = _make_fewshot_result("相关", "SELECT 1", 0.85)
        low = _make_fewshot_result("无关", "SELECT 2", 0.40)

        store = AsyncMock()
        async def mock_search(vec, top_k, score_threshold, filter=None):
            return [r for r in [high, low] if r.score >= score_threshold]
        store.search.side_effect = mock_search
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("问题", store, emb, score_threshold=0.5)
        assert len(examples) == 1
        assert examples[0].sql == "SELECT 1"

    @pytest.mark.asyncio
    async def test_empty_store_returns_empty(self):
        """无历史 → 空列表 (Agent 无 fewshot 也能生成)。"""
        store = AsyncMock()
        store.search.return_value = []
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("新问题", store, emb)
        assert examples == []

    @pytest.mark.asyncio
    async def test_embed_failure_returns_empty(self):
        """embed 失败 → 空列表 (降级, 不抛)。"""
        store = AsyncMock()
        emb = MagicMock()
        emb.embed = AsyncMock(side_effect=RuntimeError("embed fail"))

        examples = await find_fewshot_examples("问题", store, emb)
        assert examples == []

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty(self):
        """store search 失败 → 空列表 (降级)。"""
        store = AsyncMock()
        store.search = AsyncMock(side_effect=Exception("store down"))
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("问题", store, emb)
        assert examples == []

    @pytest.mark.asyncio
    async def test_example_has_question_and_sql(self):
        """FewShotExample 含 question + sql + score (供 prompt 注入)。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_fewshot_result("本月销售额", "SELECT SUM(amount) FROM orders", 0.9),
        ]
        emb = MagicMock()
        emb.embed = AsyncMock(return_value=[[0.1] * 1024])

        examples = await find_fewshot_examples("问题", store, emb)
        assert examples[0].question == "本月销售额"
        assert examples[0].sql == "SELECT SUM(amount) FROM orders"
        assert examples[0].score == pytest.approx(0.9)
