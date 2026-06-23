"""
T022: 两阶段检索 — 向量召回 + LLM 精筛

对标:
  - RAG-002 (openspec spec):
    阶段1: 向量召回 top-K (K=20) + score>=0.5 过滤
    阶段2: LLM 精筛 (prompt 含假阳性声明, 宁缺毋滥)
  - v1 教训: 检索无结果不 fallback 不随机选表 → 返回友好提示

设计要点:
  - retrieve(question, store, embedder, llm_client, data_source_id):
    阶段1: embed → store.search(top_k=20, score_threshold=0.5)
    阶段2: 召回候选 + 问题 → LLM 精筛
  - 无召回 → 空结果 + 原因 (不 fallback)
  - LLM 失败/非法 → 降级返回原始召回 (向量已过滤, 标记 degraded)
  - LLM 判断无真匹配 → 空结果 + 原因 (宁缺毋滥, 不选 score 最高)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.embedder import Embedder
from app.services.vector_store import SearchResult, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """检索结果。"""
    models: list[dict[str, Any]] = field(default_factory=list)
    # 每项: {id, name, type, score, text}
    no_match_reason: str | None = None  # 无匹配时的友好提示
    degraded: bool = False  # LLM 精筛降级标记


async def retrieve(
    question: str,
    store: VectorStore,
    embedder: Embedder,
    llm_client: Any | None = None,
    data_source_id: str | None = None,
) -> RetrievalResult:
    """两阶段检索: 向量召回 → LLM 精筛。

    Args:
        question: 用户自然语言问题
        store: VectorStore (Mock/Milvus)
        embedder: Embedder (BGE)
        llm_client: LLM client (None 则跳过阶段2, 直接返回召回)
        data_source_id: 限定数据源 (多租户/多源隔离, 对标 RAG-005)

    Returns:
        RetrievalResult — 无召回/无真匹配 → models=[] + no_match_reason
    """
    # ── 阶段 1: 向量召回 ──────────────────────────────────────
    try:
        query_vecs = await embedder.embed([question])
        query_vec = query_vecs[0]
    except Exception as e:
        logger.warning("retrieve embed 失败: %s", e)
        return RetrievalResult(no_match_reason="问题向量化失败, 无法检索")

    # 从 config 读 RAG 参数 (对标 RAG-002, 可调适配不同 embedding 模型)
    from app.core.config import get_settings
    settings = get_settings()

    filter_expr = {"data_source_id": data_source_id} if data_source_id else None
    candidates = await store.search(
        query_vec,
        top_k=settings.rag_vector_top_k,
        score_threshold=settings.rag_similarity_threshold,
        filter=filter_expr,
    )

    if not candidates:
        logger.info("retrieve: 向量召回为空 (question=%r)", question[:50])
        return RetrievalResult(
            no_match_reason="无法匹配到相关表，请换一种问法或检查数据源",
        )

    # 无 LLM → 直接返回召回结果 (阶段1 即终态)
    if llm_client is None:
        return RetrievalResult(models=_candidates_to_models(candidates))

    # ── 阶段 2: LLM 精筛 ──────────────────────────────────────
    return await _llm_refine(question, candidates, llm_client)


def _candidates_to_models(candidates: list[SearchResult]) -> list[dict[str, Any]]:
    """SearchResult → 简化 dict 列表。"""
    return [
        {
            "id": c.record.id,
            "name": c.record.metadata.get("name", c.record.id),
            "type": c.record.metadata.get("type", "model"),
            "score": c.score,
            "text": c.record.text,
        }
        for c in candidates
    ]


async def _llm_refine(
    question: str,
    candidates: list[SearchResult],
    llm_client: Any,
) -> RetrievalResult:
    """阶段 2: LLM 从召回候选里选真正相关的 (宁缺毋滥)。

    prompt 关键设计 (对标 RAG-002):
      - 声明候选来自向量检索, 可能存在假阳性
      - 要求判断语义是否真正匹配
      - 无真匹配返回空 (宁缺毋滥, 不选 score 最高)
    """
    # 构造候选清单
    candidate_lines = []
    for i, c in enumerate(candidates):
        candidate_lines.append(
            f"{i+1}. name={c.record.metadata.get('name', c.record.id)} "
            f"type={c.record.metadata.get('type', 'model')} "
            f"score={c.score:.2f} desc={c.record.text or ''}"
        )
    candidates_text = "\n".join(candidate_lines)

    prompt = (
        f"你是 BI 数据库 Schema Linking 专家。\n"
        f"用户问题: {question}\n\n"
        f"以下是向量检索召回的候选表/指标 (注意: 来自向量检索，可能存在假阳性):\n"
        f"{candidates_text}\n\n"
        f"请判断哪些候选与用户问题真正语义匹配 (维度/对象兼容)。\n"
        f"宁缺毋滥: 若无真正匹配, 返回空数组 (在 BI 场景, 返回错误数据比返回无数据更危险)。\n"
        f"只返回 JSON, 格式: "
        f'{{"models": ["匹配的name列表"], "reason": "简短理由"}}'
    )

    try:
        from app.core.config import get_settings
        settings = get_settings()
        resp = await llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("_llm_refine: LLM 返回非法 JSON, 降级返回原始召回")
        return RetrievalResult(models=_candidates_to_models(candidates), degraded=True)
    except Exception as e:
        logger.warning("_llm_refine: LLM 精筛失败, 降级返回原始召回: %s", e)
        return RetrievalResult(models=_candidates_to_models(candidates), degraded=True)

    # LLM 选出的 name → 过滤候选
    selected_names = set(parsed.get("models", []))
    if not selected_names:
        # 宁缺毋滥: LLM 判断无真匹配
        return RetrievalResult(
            no_match_reason=parsed.get("reason", "向量召回的候选均不真正匹配问题"),
        )

    refined = [
        {
            "id": c.record.id,
            "name": c.record.metadata.get("name", c.record.id),
            "type": c.record.metadata.get("type", "model"),
            "score": c.score,
            "text": c.record.text,
            "llm_selected": True,
        }
        for c in candidates
        if c.record.metadata.get("name", c.record.id) in selected_names
    ]

    logger.info("_llm_refine: %d 候选 → %d 精筛 (reason=%s)",
                len(candidates), len(refined), parsed.get("reason", ""))
    return RetrievalResult(models=refined)
