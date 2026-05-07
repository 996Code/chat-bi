"""Query result cache with Redis. Exact-match + semantic similarity cache."""
import hashlib
import json
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

# Semantic cache config
SEMANTIC_CACHE_PREFIX = "semantic:"
SEMANTIC_CACHE_TTL = settings.query_cache_ttl_seconds
SEMANTIC_CACHE_MAX_ENTRIES = 500  # per datasource


def _cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    raw = f"{tenant_id}:{question.strip().lower()}:{datasource_id}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()}"


def _semantic_cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    """Key for storing semantic cache index per datasource."""
    raw = f"{tenant_id}:{datasource_id}"
    return f"{SEMANTIC_PREFIX}{hashlib.sha256(raw.encode()).hexdigest()}"


async def cache_get(question: str, datasource_id: str, tenant_id: str = "") -> dict | None:
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw:
            logger.info("Cache HIT for key %s", key[:16])
            return json.loads(raw)
    except Exception as e:
        logger.warning("Redis cache get failed: %s", e)
    return None


async def cache_set(question: str, datasource_id: str, result: dict, tenant_id: str = "") -> None:
    if not result.get("success") or not result.get("rows"):
        return
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        await redis.setex(key, settings.query_cache_ttl_seconds, json.dumps(result, default=str))
        logger.info("Cached query result (TTL=%ds)", settings.query_cache_ttl_seconds)

        # Index key in per-datasource set for targeted cache invalidation
        index_key = _datasource_index_key(datasource_id, tenant_id)
        await redis.sadd(index_key, key)
        await redis.expire(index_key, settings.query_cache_ttl_seconds)

        # Also index for semantic lookup
        _add_to_semantic_index(question, key, datasource_id, tenant_id)
    except Exception as e:
        logger.warning("Redis cache set failed: %s", e)


async def cache_delete(question: str, datasource_id: str, tenant_id: str = "") -> None:
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as e:
        logger.warning("Redis cache delete failed: %s", e)


def _datasource_index_key(datasource_id: str, tenant_id: str = "") -> str:
    """Redis set key that tracks all cache keys for a datasource."""
    raw = f"{tenant_id}:{datasource_id}"
    return f"cache_index:{hashlib.sha256(raw.encode()).hexdigest()}"


async def cache_clear_all() -> int:
    """清除所有查询缓存（精确缓存 + 语义缓存索引）。返回清除数量。"""
    try:
        redis = await get_redis()
        deleted = 0
        async for key in redis.scan_iter(match="query:*"):
            await redis.delete(key)
            deleted += 1
        async for key in redis.scan_iter(match="semantic:*"):
            await redis.delete(key)
            deleted += 1
        async for key in redis.scan_iter(match="cache_index:*"):
            await redis.delete(key)
            deleted += 1
        logger.info("Cleared %d cache entries", deleted)
        return deleted
    except Exception as e:
        logger.warning("Cache clear all failed: %s", e)
        return 0


async def cache_clear_datasource(datasource_id: str, tenant_id: str = "") -> int:
    """清除指定数据源的所有查询缓存。返回清除数量。"""
    try:
        redis = await get_redis()
        deleted = 0

        # Delete via per-datasource index set if available
        index_key = _datasource_index_key(datasource_id, tenant_id)
        cache_keys = await redis.smembers(index_key)
        if cache_keys:
            for key in cache_keys:
                await redis.delete(key)
                deleted += 1
            await redis.delete(index_key)

        # Delete semantic cache index for this datasource
        sem_key = _semantic_cache_key("", datasource_id, tenant_id)
        if await redis.exists(sem_key):
            await redis.delete(sem_key)
            deleted += 1

        logger.info("Cleared %d cache entries for datasource %s", deleted, datasource_id)
        return deleted
    except Exception as e:
        logger.warning("Cache clear datasource failed: %s", e)
        return 0


# ── Semantic Cache ──

def _simple_similarity(a: str, b: str) -> float:
    """Quick word-overlap similarity (no embedding dependency)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


async def semantic_cache_get(question: str, datasource_id: str, tenant_id: str = "", threshold: float = 0.8) -> dict | None:
    """Find a cached result for a semantically similar question."""
    try:
        redis = await get_redis()
        index_key = _semantic_cache_key(question, datasource_id, tenant_id)
        raw = await redis.get(index_key)
        if not raw:
            return None

        index = json.loads(raw)
        best_score = 0.0
        best_key = None

        for entry in index:
            score = _simple_similarity(question, entry["q"])
            if score > best_score:
                best_score = score
                best_key = entry["k"]

        if best_score >= threshold and best_key:
            cached = await redis.get(best_key)
            if cached:
                logger.info("Semantic cache HIT (similarity=%.2f)", best_score)
                result = json.loads(cached)
                result["_semantic_match"] = True
                return result
    except Exception as e:
        logger.warning("Semantic cache lookup failed: %s", e)
    return None


async def _add_to_semantic_index(question: str, cache_key: str, datasource_id: str, tenant_id: str = "") -> None:
    """Add an entry to the semantic cache index."""
    try:
        redis = await get_redis()
        index_key = _semantic_cache_key(question, datasource_id, tenant_id)
        raw = await redis.get(index_key)
        index = json.loads(raw) if raw else []

        # Avoid duplicates
        index = [e for e in index if e["q"] != question.lower()]
        index.append({"q": question.lower().strip(), "k": cache_key, "ts": None})

        # Trim oldest entries
        if len(index) > SEMANTIC_CACHE_MAX_ENTRIES:
            index = index[-SEMANTIC_CACHE_MAX_ENTRIES:]

        await redis.set(index_key, json.dumps(index), ex=SEMANTIC_CACHE_TTL)
    except Exception as e:
        logger.warning("Semantic cache index update failed: %s", e)
