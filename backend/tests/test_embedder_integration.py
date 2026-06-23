"""
T019 集成测试: 真实 BGE-large-zh-v1.5 模型 (需要模型已下载)。

慢测试, 默认跳过 (CI 不依赖 1.3GB 模型):
    uv run pytest -m slow                 # 跑慢测试
    uv run pytest backend/tests/test_embedder_integration.py -m slow -v

对标:
  - RAG-001: 确认 BGE-large-zh-v1.5 真实输出 1024 维
  - 验证语义检索质量 (相关词相似度高, 无关词低)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.embedder import LocalEmbedder
from app.services.vector_store import MockVectorStore, VectorRecord

# 模型路径
_MODEL_PATH = get_settings().embedding_model_path
_MODEL_READY = _MODEL_PATH and Path(_MODEL_PATH).exists() and any(Path(_MODEL_PATH).iterdir())

# slow marker: 默认跳过 (需 -m slow 显式启用)
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _MODEL_READY, reason=f"模型未下载: {_MODEL_PATH}"),
]


@pytest.fixture(scope="module")
def embedder():
    """模块级共享 embedder (避免每个测试都加载 1.3GB 模型)。"""
    return LocalEmbedder(model_path=_MODEL_PATH, dim=1024)


class TestRealBGEDimension:
    """确认真实模型输出 1024 维 (对标 spec RAG-001)。"""

    @pytest.mark.asyncio
    async def test_single_text_1024_dim(self, embedder):
        vecs = await embedder.embed(["销售额"])
        assert len(vecs) == 1
        assert len(vecs[0]) == 1024, f"期望 1024 维, 实际 {len(vecs[0])}"

    @pytest.mark.asyncio
    async def test_batch_all_same_dim(self, embedder):
        vecs = await embedder.embed(["销售额", "订单数", "用户"])
        assert all(len(v) == 1024 for v in vecs)


class TestRealBGESemanticQuality:
    """语义质量: 相关词相似度高, 无关词低 (BGE 应 normalize 过)。"""

    @pytest.mark.asyncio
    async def test_synonym_high_similarity(self, embedder):
        """同义词相似度 > 0.6 (销售额 ≈ 营收)。"""
        from app.services.vector_store import _cosine_similarity
        vecs = await embedder.embed(["销售额", "营收"])
        sim = _cosine_similarity(vecs[0], vecs[1])
        assert sim > 0.6, f"同义词相似度应 > 0.6, 实际 {sim:.3f}"

    @pytest.mark.asyncio
    async def test_unrelated_low_similarity(self, embedder):
        """无关词相似度 < 同义词 (销售额 vs 订单数 < 销售额 vs 营收)。"""
        from app.services.vector_store import _cosine_similarity
        vecs = await embedder.embed(["销售额", "营收", "数据库密码"])
        sim_synonym = _cosine_similarity(vecs[0], vecs[1])
        sim_unrelated = _cosine_similarity(vecs[0], vecs[2])
        assert sim_synonym > sim_unrelated

    @pytest.mark.asyncio
    async def test_normalized_vectors(self, embedder):
        """BGE normalize_embeddings=True → 向量模长 ≈ 1 (余弦=点积)。"""
        import math
        vecs = await embedder.embed(["测试"])
        norm = math.sqrt(sum(x * x for x in vecs[0]))
        assert norm == pytest.approx(1.0, abs=1e-4)


class TestEndToEndRetrieval:
    """端到端: embed + MockVectorStore 检索 (不依赖 Milvus)。"""

    @pytest.mark.asyncio
    async def test_retrieve_relevant_table(self, embedder):
        """问"销售额" → 召回含 total_amount 的表, 而非无关表。"""
        store = MockVectorStore(dim=1024)

        # 索引两张表的语义描述
        table_texts = [
            "订单表 biz_orders 包含 total_amount 总金额 user_id 用户ID",
            "用户表 biz_users 包含 name 姓名 email 邮箱",
        ]
        table_vecs = await embedder.embed(table_texts)
        await store.upsert([
            VectorRecord(id="biz_orders", vector=table_vecs[0], metadata={"table": "biz_orders"}),
            VectorRecord(id="biz_users", vector=table_vecs[1], metadata={"table": "biz_users"}),
        ])

        # 查询"销售额"
        query_vec = (await embedder.embed(["销售额"]))[0]
        results = await store.search(query_vec, top_k=2, score_threshold=0.3)

        assert len(results) >= 1
        # 最相关的应该是 biz_orders (含金额)
        assert results[0].record.id == "biz_orders"
        assert results[0].score > 0.3
