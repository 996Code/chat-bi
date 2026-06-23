"""
T020: 索引构建 — SemanticModelContent → 向量索引

对标:
  - RAG-001 (openspec spec): 数据源接入时自动构建 Embedding 索引
  - T020 验收: 新数据源接入后自动建索引

设计要点:
  - build_index(content, data_source_id): content → VectorRecord → embed → upsert
  - 索引对象: Model (表名+描述+列名+类型), Metric (名+公式+描述)
  - 失败降级: embed/vector_store 失败不阻塞扫描 (对标 LLM enrich 宁缺毋滥)
  - 幂等: 同 id 覆盖 (vector_store.upsert 语义), 重复扫描不重复索引
  - data_source_id 进 metadata: 支持 RAG-005 按数据源标量过滤 / RAG-001 按源删全量

id 命名: <data_source_id>:<type>:<name>
  - 让 id 全局唯一 (跨数据源同表名不冲突)
  - type (model/metric) 便于分类过滤
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.schemas.semantic_layer import Metric, Model, SemanticModelContent
from app.services.embedder import Embedder
from app.services.vector_store import VectorRecord, VectorStore

logger = logging.getLogger(__name__)


# ── 文本序列化: 语义对象 → 可 embed 的文本 ────────────────────

def model_to_text(model: Model) -> str:
    """Model → embed 文本 (表名 + display_name + 描述 + 列名/类型)。

    列信息是 schema linking 的关键 (对标 RAG-002: 问"销售额"→含 amount 列的表)。
    """
    parts = [model.name, model.display_name]
    if model.description:
        parts.append(model.description)
    for col in model.columns:
        col_desc = col.name
        if col.display_name and col.display_name != col.name:
            col_desc += f"({col.display_name})"
        if col.data_type:
            col_desc += f"[{col.data_type}]"  # data_type 对标 RAG-005 类型约束
        parts.append(col_desc)
    return " ".join(parts)


def metric_to_text(metric: Metric) -> str:
    """Metric → embed 文本 (名 + display_name + 公式 + 描述)。"""
    parts = [metric.name, metric.display_name, metric.formula]
    if metric.condition:
        parts.append(metric.condition)
    if metric.description:
        parts.append(metric.description)
    return " ".join(parts)


# ── 索引构建 ──────────────────────────────────────────────────

@dataclass
class IndexResult:
    """build_index 结果。"""
    indexed_count: int = 0
    error: str | None = None


async def build_index(
    content: SemanticModelContent,
    data_source_id: str,
    store: VectorStore,
    embedder: Embedder,
) -> IndexResult:
    """把 SemanticModelContent 索引进向量库。

    Args:
        content: 语义层内容 (models + metrics)
        data_source_id: 数据源 id (进 metadata, 供标量过滤/全量删)
        store: VectorStore (Mock 或 Milvus)
        embedder: Embedder (BGE)

    Returns:
        IndexResult(indexed_count) — 失败时 indexed_count=0, error 非空 (不抛)

    设计:
      - 先收集所有文本 → 批量 embed (一次调用, 对标效率)
      - 失败降级: 返回 0, 不阻塞调用方 (扫描/CRUD)
    """
    if not content.models:
        return IndexResult(indexed_count=0)

    # 收集所有可索引对象 → (id, type, name, text)
    items: list[tuple[str, str, str, str]] = []
    for model in content.models:
        text = model_to_text(model)
        rid = f"{data_source_id}:model:{model.name}"
        items.append((rid, "model", model.name, text))
        for metric in model.metrics:
            mtext = metric_to_text(metric)
            mid = f"{data_source_id}:metric:{metric.name}"
            items.append((mid, "metric", metric.name, mtext))

    texts = [t for _, _, _, t in items]

    # 批量 embed
    try:
        vectors = await embedder.embed(texts)
    except Exception as e:
        logger.warning("build_index embed 失败, 跳过索引: %s", e)
        return IndexResult(indexed_count=0, error=str(e))

    # 组装 VectorRecord + 批量 upsert
    records = []
    for (rid, rtype, name, text), vec in zip(items, vectors):
        records.append(VectorRecord(
            id=rid,
            vector=vec,
            metadata={
                "data_source_id": data_source_id,
                "type": rtype,
                "name": name,
            },
            text=text,
        ))

    try:
        await store.upsert(records)
    except Exception as e:
        logger.warning("build_index upsert 失败, 跳过索引: %s", e)
        return IndexResult(indexed_count=0, error=str(e))

    logger.info("build_index: 索引 %d 条 (data_source=%s)", len(records), data_source_id)
    return IndexResult(indexed_count=len(records))
