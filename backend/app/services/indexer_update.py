"""
T021: 索引增量更新 — 语义层修改后重建索引

对标:
  - RAG-001: 语义层修改后更新索引 (非全库重建)
  - T021 验收: 语义层修改后增量更新

设计要点:
  - rebuild_index(content, data_source_id, store, embedder):
    删该 data_source 的全部旧索引 → 用新 content 重建
  - 按源维度删建, 非全库 (delete_by_filter 按 data_source_id 过滤)
  - 比逐条 diff 简单可靠: 语义层变更不频繁, 全量重建成本可接受
  - 失败降级: 不抛 (对标 build_index)

为什么删建而非 diff:
  - diff 需要 content 版本对比, 复杂且易错 (增删改列/指标/关系组合)
  - 删全量 + 重建语义清晰, 对标 RAG-001 "更新索引"
  - 触发点少 (回滚/编辑), 性能不是瓶颈
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.schemas.semantic_layer import SemanticModelContent
from app.services.embedder import Embedder
from app.services.indexer import build_index
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RebuildResult:
    """rebuild_index 结果。"""
    deleted_count: int = 0
    indexed_count: int = 0
    error: str | None = None


async def rebuild_index(
    content: SemanticModelContent,
    data_source_id: str,
    store: VectorStore,
    embedder: Embedder,
) -> RebuildResult:
    """语义层变更后重建索引 (删旧 + 建新)。

    Args:
        content: 新的语义层内容
        data_source_id: 数据源 id (按源删建, 非全库)
        store: VectorStore
        embedder: Embedder

    Returns:
        RebuildResult(deleted_count, indexed_count) — 失败时 error 非空 (不抛)

    流程:
      1. 删该 data_source 的全部旧索引 (delete_by_filter)
      2. 用新 content 重建 (build_index)
      失败任一步 → 降级返回, 不阻塞调用方
    """
    # 1. 删旧
    try:
        deleted = await store.delete_by_filter({"data_source_id": data_source_id})
    except Exception as e:
        logger.warning("rebuild_index 删旧索引失败: %s", e)
        return RebuildResult(error=str(e))

    # 2. 建新
    try:
        built = await build_index(
            content=content,
            data_source_id=data_source_id,
            store=store,
            embedder=embedder,
        )
    except Exception as e:
        # build_index 内部已降级, 这里兜底
        logger.warning("rebuild_index 建新索引失败 (旧索引已删): %s", e)
        return RebuildResult(deleted_count=deleted, error=str(e))

    logger.info(
        "rebuild_index: 删 %d + 建 %d (data_source=%s)",
        deleted, built.indexed_count, data_source_id,
    )
    return RebuildResult(
        deleted_count=deleted,
        indexed_count=built.indexed_count,
        error=built.error,
    )
