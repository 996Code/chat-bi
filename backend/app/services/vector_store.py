"""
T019 前置: 向量存储抽象层 + Mock 实现

对标:
  - RAG-001 (openspec spec): 向量索引构建 (upsert) + 增量更新 (delete + upsert)
  - RAG-002: 向量检索 (search) + score 过滤 (>= 0.5)
  - RAG-005: 标量过滤 (table_name 精确匹配，非向量检索)

设计要点 (对标 5 大设计原则 — 可演化):
  - VectorStore Protocol: 抽象接口，实现可切换 (Milvus / pgvector / Mock)
    不绑死 pymilvus，未来换 pgvector 或测试用 Mock 都不改调用方
  - MockVectorStore: 纯内存 + 余弦相似度，CI 不依赖外部服务
  - 维度由构造参数决定，不强绑 1024 (embedding 模型确定后再定 dim)

为什么先做这层:
  - embedding 维度 + Milvus 连通性都还没定，但这层接口是稳定的
  - T020 (索引构建) / T022 (检索) / T021 (增量更新) 全依赖这层抽象
  - 先把 Mock 跑通，真实 Milvus 实现等连通后填 (依赖注入切换)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class VectorRecord:
    """一条向量记录: id + 向量 + 标量元数据 + 原文(可选)。

    对标 Milvus: primary key + FLOAT_VECTOR + 标量字段。
    对标 RAG-005: metadata 里放 table_name / data_type 等供标量过滤。
    """
    id: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str | None = None  # 原文（调试/缓存用，不参与检索）


@dataclass
class SearchResult:
    """检索结果: 记录 + 相似度分数 [0, 1]。"""
    record: VectorRecord
    score: float


# ── 抽象接口 ──────────────────────────────────────────────────

@runtime_checkable
class VectorStore(Protocol):
    """向量存储抽象。

    所有实现 (Milvus/pgvector/Mock) 必须满足此接口。
    调用方 (T020 索引构建 / T022 检索) 只依赖此接口，不依赖具体实现。
    """

    async def upsert(self, records: list[VectorRecord]) -> None:
        """插入或更新（同 id 覆盖）。对标 RAG-001 增量更新。"""
        ...

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """向量检索。对标 RAG-002: top-K + score 过滤 + 标量过滤。

        Args:
            query_vector: 查询向量
            top_k: 召回数量 (默认 20，对标 RAG-002 K=20)
            score_threshold: score < 此值的结果过滤 (默认不过滤；RAG-002 用 0.5)
            filter: 标量精确匹配过滤 (对标 RAG-005 table_name)
        """
        ...

    async def delete(self, ids: list[str]) -> None:
        """按 id 删除。"""
        ...

    async def delete_by_filter(self, filter: dict[str, Any]) -> int:
        """按标量过滤批量删，返回删除数量。对标 RAG-001: 删某数据源全量向量。"""
        ...

    async def get(self, id: str) -> VectorRecord | None:
        """按 id 取（调试/测试用）。"""
        ...

    async def count(self) -> int:
        """记录总数（测试/监控用）。"""
        ...


# ── Mock 实现 (纯内存 + 余弦相似度) ───────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度 [-1, 1]。零向量 → 0。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _matches_filter(metadata: dict[str, Any], filter: dict[str, Any]) -> bool:
    """标量精确匹配: metadata 里所有 filter 键值都相等。"""
    return all(metadata.get(k) == v for k, v in filter.items())


class MockVectorStore:
    """内存向量存储，余弦相似度，CI 不依赖外部服务。

    语义与真实 Milvus 对齐:
      - upsert 同 id 覆盖
      - search 按余弦相似度降序 + top_k + score_threshold + filter
      - 维度校验 (防 schema 错配)
    """

    def __init__(self, dim: int):
        self.dim = dim
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> None:
        for rec in records:
            if len(rec.vector) != self.dim:
                raise ValueError(
                    f"vector dimension mismatch: record {rec.id} has "
                    f"dim={len(rec.vector)}, store expects dim={self.dim}"
                )
            self._records[rec.id] = rec

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        scored: list[SearchResult] = []
        for rec in self._records.values():
            if filter and not _matches_filter(rec.metadata, filter):
                continue
            score = _cosine_similarity(query_vector, rec.vector)
            if score < score_threshold:
                continue
            scored.append(SearchResult(record=rec, score=score))
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    async def delete(self, ids: list[str]) -> None:
        for id_ in ids:
            self._records.pop(id_, None)

    async def delete_by_filter(self, filter: dict[str, Any]) -> int:
        to_delete = [
            id_ for id_, rec in self._records.items()
            if _matches_filter(rec.metadata, filter)
        ]
        for id_ in to_delete:
            del self._records[id_]
        return len(to_delete)

    async def get(self, id: str) -> VectorRecord | None:
        return self._records.get(id)

    async def count(self) -> int:
        return len(self._records)


# ── 工厂 + 模块级单例 (对标 get_embedder / get_llm_client) ─────

import logging

_logger = logging.getLogger(__name__)

_vector_store: VectorStore | None = None
# 按 collection 缓存多个 store 实例 (支持 semantic_models + fewshot 等多 collection 并存)
_vector_stores: dict[str, VectorStore] = {}


def get_vector_store(collection_name: str = "semantic_models") -> VectorStore:
    """获取 VectorStore 实例 (按 collection_name 缓存, 支持多 collection 并存)。

    milvus: MilvusVectorStore (生产)
    mock:   MockVectorStore (测试/降级, 纯内存)
    Milvus 连不上时降级到 mock (fail-closed, 对标 Redis/Milvus 降级模式)。

    collection 名隔离: 拼上 config.vector_store_collection_prefix 前缀,
    测试/开发/多实例互不串数据 (对标 PG 库隔离; 一次配置, 全局生效)。
    向后兼容: 无参调用 (默认 semantic_models) 仍走 _vector_store 单例。
    """
    global _vector_store
    from app.core.config import get_settings
    settings = get_settings()
    # 实际 collection 名 = 前缀 + 逻辑名; 缓存/Milvus/单例判定全程用 actual
    actual = f"{settings.vector_store_collection_prefix}{collection_name}"

    # 向后兼容: 默认 collection 走原单例
    is_default = collection_name == "semantic_models"
    if is_default and _vector_store is not None:
        return _vector_store
    # 多 collection 缓存
    if actual in _vector_stores:
        return _vector_stores[actual]

    backend = settings.vector_store_backend
    store: VectorStore
    if backend == "milvus":
        try:
            from app.core.milvus_client import get_milvus_client
            from app.services.milvus_vector_store import MilvusVectorStore
            client = get_milvus_client()
            store = MilvusVectorStore(
                client=client,
                collection_name=actual,
                dim=settings.embedding_dim,
            )
            _logger.info("VectorStore: Milvus (collection=%s, dim=%d)",
                         actual, settings.embedding_dim)
        except Exception as e:
            _logger.warning("Milvus 不可用, VectorStore 降级为 Mock: %s", e)
            store = MockVectorStore(dim=settings.embedding_dim)
    else:
        store = MockVectorStore(dim=settings.embedding_dim)
        _logger.info("VectorStore: Mock (backend=%s, collection=%s)", backend, actual)

    _vector_stores[actual] = store
    if is_default:
        _vector_store = store
    return store


def reset_vector_store() -> None:
    """重置单例 (测试用)。"""
    global _vector_store
    _vector_store = None
    _vector_stores.clear()
