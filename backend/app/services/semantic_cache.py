"""
T023: 语义缓存 — 相似问题复用 SQL

对标:
  - RAG-003 (openspec spec): 问题 embed → 查相似问题 (余弦>0.95) → 复用 SQL
  - Claude Code: Session Memory 直挂 (省 API 调用)

设计要点:
  - 用 VectorStore 抽象做缓存 (不绑死 Redis; Mock/Milvus 均可)
    spec 说"Redis 存储 embedding 向量", 但 VectorStore 抽象已统一
    Mock/Milvus 都能存向量, 且语义缓存和 RAG 索引共用一套检索
  - threshold 0.95 (config.redis_semantic_cache_threshold)
  - 失败降级: embed/store 出错 → miss/skip (不报错, 对标 fail-closed)

为什么不单独用 Redis:
  - Redis 存向量要先算余弦相似度 (Redis 不原生支持), 还得自己维护
  - VectorStore 抽象已封装 search+score过滤, 复用更简洁
  - 真要 Redis 可实现 RedisVectorStore, 接口不变
"""
from __future__ import annotations

import hashlib
import logging

from app.services.embedder import Embedder
from app.services.vector_store import VectorRecord, VectorStore

logger = logging.getLogger(__name__)


class SemanticCache:
    """语义缓存: 相似问题 (余弦>threshold) 复用 SQL。

    用 VectorStore 存 question→sql 映射, 复用向量检索做相似匹配。
    """

    def __init__(self, store: VectorStore, embedder: Embedder, threshold: float = 0.95):
        self._store = store
        self._embedder = embedder
        self._threshold = threshold

    async def get(self, question: str) -> str | None:
        """查缓存: 相似度 >= threshold → 返回缓存的 SQL, 否则 None。

        失败降级返回 None (miss), 不抛异常。
        """
        try:
            vecs = await self._embedder.embed([question])
            query_vec = vecs[0]
            results = await self._store.search(
                query_vec,
                top_k=1,
                score_threshold=self._threshold,
            )
        except Exception as e:
            logger.debug("SemanticCache.get 降级 miss: %s", e)
            return None

        if not results:
            return None

        # 取最高分的一条
        best = results[0]
        sql = best.record.metadata.get("sql")
        if sql:
            logger.info("SemanticCache 命中 (score=%.3f)", best.score)
        return sql

    async def put(self, question: str, sql: str) -> None:
        """存缓存: question → sql。

        失败静默跳过 (不抛, 不影响主流程)。
        """
        try:
            vecs = await self._embedder.embed([question])
            vec = vecs[0]
        except Exception as e:
            logger.debug("SemanticCache.put embed 失败, 跳过: %s", e)
            return

        # id 用 question hash, 幂等 (同问题重复 put 不堆积)
        qid = "cache:" + hashlib.sha256(question.encode()).hexdigest()[:16]
        record = VectorRecord(
            id=qid,
            vector=vec,
            metadata={"sql": sql, "question": question},
            text=question,
        )
        try:
            await self._store.upsert([record])
        except Exception as e:
            logger.debug("SemanticCache.put 存储失败, 跳过: %s", e)


# ── 模块级单例 (对标 get_embedder / get_vector_store) ─────────

_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache | None:
    """获取全局 SemanticCache 单例。

    返回 None 表示缓存不可用 (降级, 调用方应跳过缓存逻辑)。
    """
    global _cache
    if _cache is not None:
        return _cache

    from app.core.config import get_settings
    settings = get_settings()
    try:
        from app.services.embedder import get_embedder
        from app.services.vector_store import get_vector_store
        _cache = SemanticCache(
            store=get_vector_store(collection_name="semantic_cache"),
            embedder=get_embedder(),
            threshold=settings.redis_semantic_cache_threshold,
        )
    except Exception as e:
        logger.warning("SemanticCache 不可用, 降级跳过缓存: %s", e)
        return None
    return _cache


def reset_semantic_cache() -> None:
    """重置单例 (测试用)。"""
    global _cache
    _cache = None
