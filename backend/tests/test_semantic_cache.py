"""
T023: 语义缓存 — 单元测试

对标:
  - RAG-003 (openspec spec): 问题 embed → 查相似问题 (余弦>0.95) → 复用 SQL
  - 验收: "本月销售额"→"这个月的营收" 语义缓存命中

设计要点:
  - 用 VectorStore 抽象做缓存 (不绑死 Redis; Mock/Milvus 均可)
  - SemanticCache: get(question) → 命中返回缓存的 SQL; put(question, sql)
  - 相似度阈值 0.95 (config.redis_semantic_cache_threshold)
  - Redis/vector_store 连不上 → 降级跳过缓存 (不报错, 每次都 miss)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.semantic_cache import SemanticCache


def _make_embedder(return_vec=None):
    """构造 mock embedder, 每次返回固定向量或递增向量。"""
    emb = MagicMock()
    emb.embed = AsyncMock(return_value=[return_vec or [0.1] * 1024])
    emb.dim = 1024
    return emb


class TestSemanticCache:
    """语义缓存: 相似问题命中。"""

    @pytest.mark.asyncio
    async def test_put_then_get_exact_match(self):
        """put 后, 相同问题 get 命中 (相似度 1.0)。"""
        store = AsyncMock()
        store.search.return_value = []  # 初始空
        emb = _make_embedder()

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)

        await cache.put("本月销售额", "SELECT SUM(total_amount) FROM orders")
        # put 会 upsert
        store.upsert.assert_called_once()

        # get: 假设向量检索返回命中 (相似度 1.0)
        store.search.return_value = [MagicMock(
            record=MagicMock(
                id="q1", vector=[0.1] * 1024,
                metadata={"sql": "SELECT SUM(total_amount) FROM orders"},
                text="本月销售额",
            ),
            score=1.0,
        )]
        hit = await cache.get("本月销售额")
        assert hit is not None
        assert hit == "SELECT SUM(total_amount) FROM orders"

    @pytest.mark.asyncio
    async def test_get_below_threshold_returns_none(self):
        """相似度 < 0.95 → miss (不返回低置信度的缓存)。

        真实 VectorStore 会按 score_threshold 过滤, 这里 mock 模拟该行为:
        score < threshold 的不返回。
        """
        low_score_result = MagicMock(
            record=MagicMock(
                id="q1", vector=[0.1] * 1024,
                metadata={"sql": "SELECT * FROM users"},
                text="用户列表",
            ),
            score=0.80,
        )

        store = AsyncMock()
        # mock search 按 score_threshold 过滤 (模拟真实行为)
        async def mock_search(vec, top_k, score_threshold, filter=None):
            if low_score_result.score >= score_threshold:
                return [low_score_result]
            return []
        store.search.side_effect = mock_search
        emb = _make_embedder()

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        hit = await cache.get("本月销售额")
        assert hit is None  # 低于阈值, miss

    @pytest.mark.asyncio
    async def test_get_empty_cache_returns_none(self):
        """空缓存 → miss。"""
        store = AsyncMock()
        store.search.return_value = []
        emb = _make_embedder()

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        hit = await cache.get("任意问题")
        assert hit is None

    @pytest.mark.asyncio
    async def test_put_stores_question_and_sql(self):
        """put 存 question + sql 到 vector_store。"""
        store = AsyncMock()
        emb = _make_embedder()

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        await cache.put("本月销售额", "SELECT SUM(amount) FROM orders")

        store.upsert.assert_called_once()
        records = store.upsert.call_args.kwargs.get("records") or store.upsert.call_args[0][0]
        assert len(records) == 1
        assert records[0].metadata["sql"] == "SELECT SUM(amount) FROM orders"
        assert records[0].text == "本月销售额"

    @pytest.mark.asyncio
    async def test_get_embed_failure_returns_none(self):
        """embed 失败 → miss (降级, 不抛)。"""
        store = AsyncMock()
        emb = MagicMock()
        emb.embed = AsyncMock(side_effect=RuntimeError("embed fail"))

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        hit = await cache.get("问题")
        assert hit is None

    @pytest.mark.asyncio
    async def test_put_embed_failure_silent(self):
        """put 时 embed 失败 → 静默跳过 (不抛, 不存)。"""
        store = AsyncMock()
        emb = MagicMock()
        emb.embed = AsyncMock(side_effect=RuntimeError("embed fail"))

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        await cache.put("问题", "SELECT 1")  # 不应抛
        store.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_search_failure_returns_none(self):
        """vector_store search 失败 → miss (降级)。"""
        store = AsyncMock()
        store.search = AsyncMock(side_effect=Exception("store down"))
        emb = _make_embedder()

        cache = SemanticCache(store=store, embedder=emb, threshold=0.95)
        hit = await cache.get("问题")
        assert hit is None
