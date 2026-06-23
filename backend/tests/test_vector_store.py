"""
T019 前置: 向量存储抽象层 — 单元测试

对标:
  - RAG-001/002 (openspec spec): 向量存储需支持 upsert/search/delete
  - 设计: VectorStore Protocol 抽象，实现可切换 (Milvus/pgvector/Mock)
    LLM/Embedding 维度和 Milvus 连通性都不影响这层接口测试

设计要点:
  - Protocol (duck typing): 不绑死 pymilvus，pgvector/Mock 都能实现
  - MockVectorStore: 纯内存，余弦相似度，CI 不依赖外部服务
  - 维度由调用方决定 (dim 参数)，不强绑 1024
"""
from __future__ import annotations

import pytest

from app.services.vector_store import (
    MockVectorStore,
    SearchResult,
    VectorRecord,
    VectorStore,
)


# ── VectorRecord ──────────────────────────────────────────────

class TestVectorRecord:
    """向量记录：id + 向量 + 标量元数据。"""

    def test_create_record(self):
        rec = VectorRecord(
            id="t1",
            vector=[0.1, 0.2, 0.3],
            metadata={"table_name": "biz_orders", "type": "model"},
        )
        assert rec.id == "t1"
        assert rec.vector == [0.1, 0.2, 0.3]
        assert rec.metadata["table_name"] == "biz_orders"

    def test_record_with_text(self):
        rec = VectorRecord(id="t1", vector=[1.0], metadata={}, text="销售额")
        assert rec.text == "销售额"


# ── SearchResult ──────────────────────────────────────────────

class TestSearchResult:
    """检索结果：记录 + 相似度分数。"""

    def test_create_result(self):
        rec = VectorRecord(id="t1", vector=[0.1], metadata={})
        result = SearchResult(record=rec, score=0.92)
        assert result.score == pytest.approx(0.92)
        assert result.record.id == "t1"


# ── MockVectorStore: upsert ───────────────────────────────────

class TestMockUpsert:
    """插入/更新向量。"""

    @pytest.mark.asyncio
    async def test_upsert_inserts_new(self):
        store = MockVectorStore(dim=3)
        rec = VectorRecord(id="t1", vector=[0.1, 0.2, 0.3], metadata={})
        await store.upsert([rec])
        assert await store.count() == 1

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing(self):
        """同 id 再 upsert → 覆盖（不是追加）。"""
        store = MockVectorStore(dim=3)
        rec1 = VectorRecord(id="t1", vector=[0.1, 0.2, 0.3], metadata={"v": 1})
        rec2 = VectorRecord(id="t1", vector=[0.4, 0.5, 0.6], metadata={"v": 2})
        await store.upsert([rec1])
        await store.upsert([rec2])
        assert await store.count() == 1  # 不追加
        got = await store.get("t1")
        assert got.metadata["v"] == 2  # 覆盖

    @pytest.mark.asyncio
    async def test_upsert_dimension_mismatch_raises(self):
        """向量维度 ≠ store.dim → 拒绝（防 schema 错配）。"""
        store = MockVectorStore(dim=3)
        bad = VectorRecord(id="t1", vector=[0.1, 0.2], metadata={})  # dim=2
        with pytest.raises(ValueError, match="dimension"):
            await store.upsert([bad])

    @pytest.mark.asyncio
    async def test_upsert_batch(self):
        store = MockVectorStore(dim=2)
        recs = [
            VectorRecord(id=f"t{i}", vector=[float(i), float(i)], metadata={})
            for i in range(5)
        ]
        await store.upsert(recs)
        assert await store.count() == 5


# ── MockVectorStore: search ───────────────────────────────────

class TestMockSearch:
    """向量检索 + 余弦相似度 + top-K + score 过滤。"""

    @pytest.mark.asyncio
    async def test_search_returns_top_k(self):
        store = MockVectorStore(dim=2)
        await store.upsert([
            VectorRecord(id="a", vector=[1.0, 0.0], metadata={}),  # 与 query 正交
            VectorRecord(id="b", vector=[1.0, 1.0], metadata={}),  # 最相似
            VectorRecord(id="c", vector=[0.0, 1.0], metadata={}),
        ])
        results = await store.search([1.0, 1.0], top_k=2)
        assert len(results) == 2
        assert results[0].record.id == "b"  # 最相似排第一
        assert results[0].score == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_search_score_filter(self):
        """score < threshold 的结果过滤掉（对标 RAG-002 score>=0.5）。"""
        store = MockVectorStore(dim=2)
        await store.upsert([
            VectorRecord(id="sim", vector=[1.0, 1.0], metadata={}),
            VectorRecord(id="orth", vector=[1.0, 0.0], metadata={}),  # 与 [0,1] 正交 score=0
        ])
        results = await store.search([0.0, 1.0], top_k=5, score_threshold=0.5)
        ids = [r.record.id for r in results]
        assert "sim" not in ids or results[0].score >= 0.5
        # 正交的应该被过滤 (score≈0 < 0.5)
        assert "orth" not in ids

    @pytest.mark.asyncio
    async def test_search_empty_store(self):
        store = MockVectorStore(dim=3)
        results = await store.search([0.1, 0.2, 0.3], top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_scalar_filter(self):
        """标量过滤（对标 RAG-005: table_name 精确匹配）。"""
        store = MockVectorStore(dim=2)
        await store.upsert([
            VectorRecord(id="a", vector=[1.0, 1.0], metadata={"data_source_id": "ds1"}),
            VectorRecord(id="b", vector=[1.0, 1.0], metadata={"data_source_id": "ds2"}),
        ])
        results = await store.search(
            [1.0, 1.0], top_k=5,
            filter={"data_source_id": "ds1"},
        )
        assert all(r.record.metadata["data_source_id"] == "ds1" for r in results)
        assert len(results) == 1


# ── MockVectorStore: delete ───────────────────────────────────

class TestMockDelete:
    """删除向量（对标 RAG-001 增量更新: 语义层改了要删旧向量）。"""

    @pytest.mark.asyncio
    async def test_delete_by_id(self):
        store = MockVectorStore(dim=2)
        await store.upsert([VectorRecord(id="t1", vector=[1.0, 1.0], metadata={})])
        await store.delete(["t1"])
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_noop(self):
        store = MockVectorStore(dim=2)
        await store.delete(["ghost"])  # 不抛
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_delete_by_filter(self):
        """按标量过滤批量删（对标 RAG-001: 删某数据源的所有向量）。"""
        store = MockVectorStore(dim=2)
        await store.upsert([
            VectorRecord(id="a", vector=[1.0, 1.0], metadata={"ds": "x"}),
            VectorRecord(id="b", vector=[1.0, 1.0], metadata={"ds": "x"}),
            VectorRecord(id="c", vector=[1.0, 1.0], metadata={"ds": "y"}),
        ])
        n = await store.delete_by_filter({"ds": "x"})
        assert n == 2
        assert await store.count() == 1


# ── VectorStore Protocol 兼容性 ───────────────────────────────

class TestProtocolConformance:
    """MockVectorStore 必须满足 VectorStore Protocol (结构子类型)。"""

    def test_mock_is_vector_store(self):
        store: VectorStore = MockVectorStore(dim=3)
        # 静态类型检查层面也成立 (mypy/pyright)
        assert hasattr(store, "upsert")
        assert hasattr(store, "search")
        assert hasattr(store, "delete")
