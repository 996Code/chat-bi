"""
T024: Few-shot 历史匹配 — 召回相似问题的审核 SQL

对标:
  - RAG-004 (openspec spec): 审核过的 Question-SQL Pair 作 few-shot, 最多 3 条
  - Claude Code: Relevant Recall (按需召回, 不全量灌入 prompt)

设计要点:
  - find_fewshot_examples(question, store, embedder, top_k=3):
    embed 问题 → 检索 fewshot collection → 返回最多 top_k 条
  - 独立 collection (fewshot), 和 schema 索引分开
  - score 阈值过滤 (宁缺毋滥, 弱相关示例会误导 SQL 生成)
  - 失败降级: 返回空列表 (Agent 无 fewshot 也能生成)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.embedder import Embedder
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# fewshot 默认阈值 (历史 SQL 匹配要求较高相关性, 否则误导)
FEWSHOT_SCORE_THRESHOLD = 0.5


@dataclass
class FewShotExample:
    """一条 few-shot 示例 (供 prompt 注入)。"""
    question: str
    sql: str
    score: float


async def find_fewshot_examples(
    question: str,
    store: VectorStore,
    embedder: Embedder,
    data_source_id: str | None = None,
    top_k: int = 3,
    score_threshold: float = FEWSHOT_SCORE_THRESHOLD,
) -> list[FewShotExample]:
    """检索相似历史 SQL 作为 few-shot 示例。

    Args:
        question: 当前用户问题
        store: VectorStore (fewshot collection)
        embedder: Embedder
        data_source_id: 数据源 ID (标量过滤, 防跨数据源召回; None 不过滤)
        top_k: 最多返回条数 (默认 3, 对标 RAG-004 防 prompt token 爆炸)
        score_threshold: score 低于此值不返回 (宁缺毋滥)

    Returns:
        FewShotExample 列表, 按 score 降序, 最多 top_k 条。
        失败/无结果返回空列表 (不抛)。
    """
    try:
        vecs = await embedder.embed([question])
        query_vec = vecs[0]
    except Exception as e:
        logger.debug("find_fewshot embed 失败, 返回空: %s", e)
        return []

    # 标量过滤: 按 data_source_id 精确匹配, 防跨数据源召回错误 SQL
    search_filter = {"data_source_id": data_source_id} if data_source_id else None

    try:
        results = await store.search(
            query_vec,
            top_k=top_k,
            score_threshold=score_threshold,
            filter=search_filter,
        )
    except Exception as e:
        logger.debug("find_fewshot search 失败, 返回空: %s", e)
        return []

    examples: list[FewShotExample] = []
    for r in results:
        sql = r.record.metadata.get("sql")
        if not sql:
            continue
        examples.append(FewShotExample(
            question=r.record.metadata.get("question", r.record.text or ""),
            sql=sql,
            score=r.score,
        ))

    logger.info(
        "find_fewshot: %d 候选 → %d 示例 (question=%r)",
        len(results), len(examples), question[:50],
    )
    return examples


def format_fewshot_prompt(examples: list[FewShotExample]) -> str:
    """把 few-shot 示例格式化成 prompt 片段 (供 Agent SQL 生成注入)。

    对标 RAG-004: 注入历史审核 SQL 作参考。
    """
    if not examples:
        return ""
    lines = ["以下是相似问题的参考 SQL (已审核, 可借鉴写法):"]
    for i, ex in enumerate(examples, 1):
        lines.append(f"{i}. 问题: {ex.question}")
        lines.append(f"   SQL: {ex.sql}")
    return "\n".join(lines)


async def index_fewshot_example(
    question: str,
    sql: str,
    embedder: Embedder,
    data_source_id: str,
    example_id: str | None = None,
) -> None:
    """把审核通过的 Question-SQL Pair 写入 fewshot 向量库 (数据回流)。

    对标 RAG-004: 反馈审核通过 → 回流知识库 → 后续相似问题可召回作 few-shot。
    失败降级只记日志 (不阻塞审核流程)。

    Args:
        question: 用户原始问题 (作为向量化的文本 + 检索时的匹配键)
        sql: 审核通过的 SQL (存 metadata, 召回时取出注入 prompt)
        embedder: Embedder
        data_source_id: 数据源 ID (标量过滤, 防跨数据源召回)
        example_id: 唯一 ID (None 则用 question hash)
    """
    import hashlib
    from app.services.vector_store import VectorRecord, get_vector_store

    try:
        vecs = await embedder.embed([question])
        vector = vecs[0]
    except Exception as e:
        logger.warning("fewshot 索引 embed 失败 (不阻塞): %s", e)
        return

    if not example_id:
        # ID 包含 data_source_id, 防止同问题跨数据源覆盖
        example_id = hashlib.md5(f"{data_source_id}:{question}".encode("utf-8")).hexdigest()

    store = get_vector_store("fewshot")
    try:
        await store.upsert([VectorRecord(
            id=example_id,
            vector=vector,
            text=question,
            metadata={"question": question, "sql": sql, "data_source_id": data_source_id},
        )])
        logger.info("fewshot 示例已索引: %s (ds=%s)", question[:50], data_source_id[:8])
    except Exception as e:
        logger.warning("fewshot 索引 upsert 失败 (不阻塞): %s", e)
