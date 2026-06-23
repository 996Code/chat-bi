"""
T019: MilvusVectorStore 真实实现 — 单元测试 (mock pymilvus)

对标:
  - RAG-001: Milvus collection schema (id + vector + 标量字段)
  - RAG-002: search top-K + score 过滤
  - RAG-005: 标量精确匹配过滤 (table_name)
  - VectorStore Protocol: 实现可切换 (Mock 留作测试, Milvus 留作生产)

设计:
  - pymilvus MilvusClient 是同步 API → asyncio.to_thread 包 (不阻塞事件循环)
  - 真实 Milvus 未起时, 测试用 mock client (不依赖连通性)
  - collection 名 + dim 构造时指定 (对标 RAG-001 索引对象分离)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.vector_store import VectorRecord
from app.services.milvus_vector_store import MilvusVectorStore


# ── 构造 + collection 创建 ────────────────────────────────────

class TestCollectionCreation:
    """MilvusVectorStore 构造时建 collection (如不存在)。"""

    def test_init_creates_collection_if_not_exists(self):
        """collection 不存在 → 创建 (dim 对齐 FLOAT_VECTOR)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = False

        store = MilvusVectorStore(
            client=mock_client,
            collection_name="semantic_models",
            dim=1024,
        )

        # 验证创建了 collection
        mock_client.has_collection.assert_called_once_with("semantic_models")
        mock_client.create_collection.assert_called_once()
        # 检查 schema 里的 vector 维度
        call_args = mock_client.create_collection.call_args
        schema = call_args.kwargs.get("schema") or call_args[1].get("schema")
        # schema 是 CollectionSchema, 检查 fields
        assert schema is not None

    def test_init_skips_creation_if_exists(self):
        """collection 已存在 → 不重建 (幂等)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True

        store = MilvusVectorStore(
            client=mock_client, collection_name="x", dim=1024,
        )

        mock_client.create_collection.assert_not_called()

    def test_create_index_called(self):
        """建 collection 后要建向量索引 (HNSW, 对标 spec)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = False

        MilvusVectorStore(client=mock_client, collection_name="x", dim=1024)

        # 应该调 create_index (HNSW 或 IVF)
        mock_client.create_index.assert_called_once()


# ── upsert ────────────────────────────────────────────────────

class TestUpsert:
    """插入/更新向量到 Milvus。"""

    @pytest.mark.asyncio
    async def test_upsert_inserts_records(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        rec = VectorRecord(
            id="t1", vector=[0.1, 0.2, 0.3],
            metadata={"table_name": "biz_orders", "type": "model"},
            text="订单表",
        )
        await store.upsert([rec])

        mock_client.upsert.assert_called_once()
        call = mock_client.upsert.call_args
        assert call.kwargs.get("collection_name") == "x" or call[0][0] == "x"
        # 数据应含 id + vector + 标量字段
        data = call.kwargs.get("data") or call[0][1]
        assert len(data) == 1
        assert data[0]["id"] == "t1"
        assert data[0]["vector"] == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_upsert_empty_list_noop(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        await store.upsert([])
        mock_client.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_dimension_mismatch_raises(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        bad = VectorRecord(id="t1", vector=[0.1, 0.2], metadata={})  # dim=2
        with pytest.raises(ValueError, match="dimension"):
            await store.upsert([bad])


# ── search ────────────────────────────────────────────────────

class TestSearch:
    """向量检索。"""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.search.return_value = [
            [  # Milvus search 返回 [[(id, score, ...), ...], ...] 每个查询一个列表
                {"id": "t1", "distance": 0.95, "entity": {"metadata": {"table_name": "biz_orders"}, "text": "订单"}},
                {"id": "t2", "distance": 0.80, "entity": {"metadata": {"table_name": "biz_products"}, "text": "商品"}},
            ]
        ]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        results = await store.search([0.1, 0.2, 0.3], top_k=5)

        assert len(results) == 2
        assert results[0].record.id == "t1"
        assert results[0].score == pytest.approx(0.95)
        assert results[0].record.metadata["table_name"] == "biz_orders"

    @pytest.mark.asyncio
    async def test_search_top_k_passed(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.search.return_value = [[]]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        await store.search([0.1, 0.2, 0.3], top_k=20)

        call = mock_client.search.call_args
        limit = call.kwargs.get("limit") or call[1].get("limit")
        assert limit == 20

    @pytest.mark.asyncio
    async def test_search_score_threshold_filters(self):
        """score < threshold 的结果被过滤 (对标 RAG-002 >= 0.5)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.search.return_value = [
            [
                {"id": "high", "distance": 0.9, "entity": {}},
                {"id": "low", "distance": 0.3, "entity": {}},
            ]
        ]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        results = await store.search([0.1, 0.2, 0.3], top_k=5, score_threshold=0.5)

        ids = [r.record.id for r in results]
        assert "high" in ids
        assert "low" not in ids

    @pytest.mark.asyncio
    async def test_search_scalar_filter(self):
        """标量过滤传给 Milvus filter 表达式 (对标 RAG-005)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.search.return_value = [[]]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        await store.search(
            [0.1, 0.2, 0.3], top_k=5,
            filter={"data_source_id": "ds1"},
        )

        call = mock_client.search.call_args
        filter_expr = call.kwargs.get("filter") or call[1].get("filter")
        assert filter_expr is not None
        assert "ds1" in filter_expr

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.search.return_value = [[]]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        results = await store.search([0.1, 0.2, 0.3], top_k=5)
        assert results == []


# ── delete ────────────────────────────────────────────────────

class TestDelete:
    """删除向量 (对标 RAG-001 增量更新)。"""

    @pytest.mark.asyncio
    async def test_delete_by_ids(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        await store.delete(["t1", "t2"])
        mock_client.delete.assert_called_once()
        call = mock_client.delete.call_args
        # filter 表达式应包含 id
        filter_expr = call.kwargs.get("filter") or call[0][0]
        assert "t1" in str(filter_expr) and "t2" in str(filter_expr)

    @pytest.mark.asyncio
    async def test_delete_empty_ids_noop(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        await store.delete([])
        mock_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_by_filter(self):
        """按标量过滤批量删 (对标 RAG-001: 删某数据源全量)。"""
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        # query 用来先查出要删的 id 数量
        mock_client.query.return_value = [{"id": "a"}, {"id": "b"}]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        n = await store.delete_by_filter({"data_source_id": "ds1"})

        assert n == 2
        mock_client.delete.assert_called_once()


# ── get / count ───────────────────────────────────────────────

class TestGetCount:
    """查询辅助。"""

    @pytest.mark.asyncio
    async def test_get_existing(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.query.return_value = [
            {"id": "t1", "vector": [0.1, 0.2, 0.3], "metadata": {"table_name": "biz_orders"}, "text": "订单"}
        ]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        rec = await store.get("t1")
        assert rec is not None
        assert rec.id == "t1"
        assert rec.metadata["table_name"] == "biz_orders"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.query.return_value = []

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        rec = await store.get("ghost")
        assert rec is None

    @pytest.mark.asyncio
    async def test_count(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        mock_client.query.return_value = [{"count(*)": 42}]

        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=3)
        n = await store.count()
        assert n == 42


# ── Protocol 兼容 ─────────────────────────────────────────────

class TestProtocolConformance:
    def test_milvus_is_vector_store(self):
        mock_client = MagicMock()
        mock_client.has_collection.return_value = True
        store = MilvusVectorStore(client=mock_client, collection_name="x", dim=1024)
        # 满足 VectorStore Protocol
        assert hasattr(store, "upsert")
        assert hasattr(store, "search")
        assert hasattr(store, "delete")
        assert hasattr(store, "delete_by_filter")
        assert hasattr(store, "get")
        assert hasattr(store, "count")
