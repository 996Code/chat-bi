"""
T019: MilvusVectorStore — Milvus 真实实现

对标:
  - RAG-001: collection schema (id + vector + 标量字段)
  - RAG-002: search top-K + score 过滤 (>= threshold)
  - RAG-005: 标量精确匹配过滤 (table_name)
  - VectorStore Protocol: 与 MockVectorStore 同接口, 实现可切换

设计要点:
  - pymilvus MilvusClient 是同步 API → asyncio.to_thread 包 (不阻塞事件循环)
    对标 LocalEmbedder 的做法
  - metadata 用 JSON 字段存 (动态, 不用为每个 key 建 column)
  - score: Milvus 内积距离 (normalize 向量后 = 余弦相似度)
  - client 可注入 (测试用 mock; 生产用 get_milvus_client)

为什么用 JSON 字段存 metadata:
  - 标量过滤场景固定 (table_name / data_source_id), 但 metadata 内容多变
  - JSON 字段 + Milvus 标量过滤表达式, 灵活且 schema 稳定
  - 对标海泰: 标量过滤用 Milvus filter 而非向量检索
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.services.vector_store import SearchResult, VectorRecord, VectorStore

logger = logging.getLogger(__name__)


def _build_filter_expr(filter: dict[str, Any] | None) -> str:
    """标量过滤 dict → Milvus filter 表达式。

    {"table_name": "biz_orders", "type": "model"}
    → 'table_name == "biz_orders" and type == "model"'

    metadata 存在 JSON 字段里, 用 json_contains 风格; 但常用标量字段
    提到独立 column 做精确匹配更快。这里假设 metadata 整体是 JSON 字段,
    用 JSON 字段表达式: metadata["key"] == value
    """
    if not filter:
        return ""
    parts = []
    for k, v in filter.items():
        if isinstance(v, str):
            parts.append(f'metadata["{k}"] == "{v}"')
        elif isinstance(v, (int, float)):
            parts.append(f'metadata["{k}"] == {v}')
        elif v is None:
            parts.append(f'metadata["{k}"] is null')
        else:
            # 其他类型转 JSON 字符串匹配
            parts.append(f'metadata["{k}"] == {json.dumps(v)}')
    return " and ".join(parts)


def _build_id_filter(ids: list[str]) -> str:
    """id 列表 → Milvus filter 表达式 (in 操作)。"""
    quoted = ", ".join(f'"{i}"' for i in ids)
    return f'id in [{quoted}]'


class MilvusVectorStore(VectorStore):
    """Milvus 向量存储实现。

    对标 VectorStore Protocol, 与 MockVectorStore 接口一致, 可依赖注入切换。
    """

    def __init__(
        self,
        client: Any,  # MilvusClient (注入; 测试用 mock)
        collection_name: str,
        dim: int,
    ):
        self._client = client
        self._collection_name = collection_name
        self._dim = dim
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """collection 不存在则创建 (id + vector + metadata + text) + 建索引, 然后 load。

        Milvus 查询/search 前必须 load collection (否则报 collection not loaded)。
        """
        if not self._client.has_collection(self._collection_name):
            from pymilvus import CollectionSchema, DataType, FieldSchema

            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self._dim),
                FieldSchema(name="metadata", dtype=DataType.JSON),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
            ]
            schema = CollectionSchema(fields=fields, auto_id=False, enable_dynamic_field=False)

            self._client.create_collection(
                collection_name=self._collection_name,
                schema=schema,
            )
            # HNSW 索引 (对标 spec: HNSW 大规模性能优)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="IP",  # Inner Product (normalize 后 = 余弦相似度)
                params={"M": 16, "efConstruction": 200},
            )
            self._client.create_index(
                collection_name=self._collection_name,
                index_params=index_params,
            )
            logger.info("Milvus collection created: %s (dim=%d)", self._collection_name, self._dim)

        # load collection (查询/search 前必须 load; 已 loaded 幂等, try/except 容错)
        try:
            self._client.load_collection(self._collection_name)
            logger.info("Milvus collection loaded: %s", self._collection_name)
        except Exception as e:
            # 已 loaded 或其他非致命错误, 不阻塞 (search 时 Milvus 会再处理)
            logger.debug("Milvus load_collection (可能已 loaded): %s", e)

    # ── upsert ────────────────────────────────────────────────

    async def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        for rec in records:
            if len(rec.vector) != self._dim:
                raise ValueError(
                    f"vector dimension mismatch: record {rec.id} has "
                    f"dim={len(rec.vector)}, store expects dim={self._dim}"
                )

        data = [
            {
                "id": rec.id,
                "vector": rec.vector,
                "metadata": rec.metadata,
                "text": rec.text or "",
            }
            for rec in records
        ]
        await asyncio.to_thread(
            self._client.upsert,
            collection_name=self._collection_name,
            data=data,
        )

    # ── search ────────────────────────────────────────────────

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 20,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        filter_expr = _build_filter_expr(filter)
        results = await asyncio.to_thread(
            self._client.search,
            collection_name=self._collection_name,
            data=[query_vector],
            anns_field="vector",
            limit=top_k,
            filter=filter_expr or None,
            search_params={"metric_type": "IP", "params": {"ef": 64}},
            output_fields=["metadata", "text"],
        )
        # results 是 [[hit, ...], ...] (每个查询一个列表, 这里只一个查询)
        hits = results[0] if results else []
        out: list[SearchResult] = []
        for hit in hits:
            # Milvus 3.0 search hit: {"id":..., "distance":..., "entity":{...}}
            score = hit.get("distance", hit.get("score", 0.0))
            if score < score_threshold:
                continue
            entity = hit.get("entity", {})
            metadata = entity.get("metadata", {}) or {}
            text = entity.get("text")
            rec = VectorRecord(
                id=hit["id"],
                vector=query_vector,  # search 不返回 vector, 用 query 占位
                metadata=metadata,
                text=text,
            )
            out.append(SearchResult(record=rec, score=score))
        return out

    # ── delete ────────────────────────────────────────────────

    async def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        filter_expr = _build_id_filter(ids)
        await asyncio.to_thread(
            self._client.delete,
            collection_name=self._collection_name,
            filter=filter_expr,
        )

    async def delete_by_filter(self, filter: dict[str, Any]) -> int:
        """先查出匹配的 id 数量, 再删。"""
        filter_expr = _build_filter_expr(filter)
        existing = await asyncio.to_thread(
            self._client.query,
            collection_name=self._collection_name,
            filter=filter_expr,
            output_fields=["id"],
        )
        count = len(existing)
        if count > 0:
            ids = [row["id"] for row in existing]
            await self.delete(ids)
        return count

    # ── get / count ───────────────────────────────────────────

    async def get(self, id: str) -> VectorRecord | None:
        rows = await asyncio.to_thread(
            self._client.query,
            collection_name=self._collection_name,
            filter=f'id == "{id}"',
            output_fields=["vector", "metadata", "text"],
        )
        if not rows:
            return None
        row = rows[0]
        return VectorRecord(
            id=id,
            vector=row.get("vector", []),
            metadata=row.get("metadata", {}) or {},
            text=row.get("text"),
        )

    async def count(self) -> int:
        rows = await asyncio.to_thread(
            self._client.query,
            collection_name=self._collection_name,
            filter="",
            output_fields=["count(*)"],
        )
        if not rows:
            return 0
        return rows[0].get("count(*)", 0)
