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
    """Model → embed 文本 (中文优先, 重复描述增强语义)。

    BGE 等中文向量模型对中文文本质量高度敏感。
    设计原则:
      - 中文描述重复2遍, 提升核心语义权重
      - display_name (中文名) 优先, 英文表名放后面
      - 列信息只保留 display_name (中文注释), 不放英文列名
      - data_type 不进文本 (对语义匹配无帮助, 反增噪声)
    """
    parts: list[str] = []
    # 中文描述重复2遍 — 核心语义
    if model.description:
        parts.append(model.description)
        parts.append(model.description)
    # display_name (中文名)
    if model.display_name and model.display_name != model.name:
        parts.append(model.display_name)
    # 英文表名放最后 (向量模型对英文不敏感, 但保留给精确匹配)
    parts.append(f"表名{model.name}")
    # 列: 只取中文 display_name, 不放英文列名
    col_names: list[str] = []
    for col in model.columns:
        if col.display_name and col.display_name != col.name:
            col_names.append(col.display_name)
    if col_names:
        parts.append("字段:" + ",".join(col_names))
    return "。".join(parts)


def metric_to_text(metric: Metric) -> str:
    """Metric → embed 文本 (中文优先, display_name + 描述 + 公式)。"""
    parts: list[str] = []
    if metric.description:
        parts.append(metric.description)
    if metric.display_name and metric.display_name != metric.name:
        parts.append(metric.display_name)
    if metric.condition:
        parts.append(metric.condition)
    parts.append(f"指标{metric.name}")
    parts.append(metric.formula)
    return "。".join(parts)


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
