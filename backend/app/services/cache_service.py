"""Query result cache with Redis connection pool and in-memory fallback."""
import hashlib
import json
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

# In-memory fallback (TTL-based dict)
_local_cache: dict[str, tuple[float, Any]] = {}


def _cache_key(question: str, datasource_id: str) -> str:
    """Generate cache key from question and datasource."""
    raw = f"{question.strip().lower()}:{datasource_id}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()}"


def _set_local(key: str, value: Any) -> None:
    _local_cache[key] = (time.time() + settings.query_cache_ttl_seconds, value)
    # Clean expired entries
    now = time.time()
    expired = [k for k, (exp, _) in _local_cache.items() if exp < now]
    for k in expired:
        del _local_cache[k]


def _get_local(key: str) -> Any | None:
    entry = _local_cache.get(key)
    if entry is None:
        return None
    exp, value = entry
    if time.time() > exp:
        del _local_cache[key]
        return None
    return value


async def cache_get(question: str, datasource_id: str) -> dict | None:
    """Get cached query result. Returns None on miss."""
    key = _cache_key(question, datasource_id)

    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw:
            logger.info("Redis cache HIT for key %s", key[:16])
            return json.loads(raw)
    except Exception as e:
        logger.warning("Redis cache get failed, falling back to local: %s", e)

    value = _get_local(key)
    if value:
        logger.info("Local cache HIT for key %s", key[:16])
    return value


async def cache_set(question: str, datasource_id: str, result: dict) -> None:
    """Cache query result."""
    if not result.get("success") or not result.get("rows"):
        return  # Don't cache errors or empty results

    key = _cache_key(question, datasource_id)

    try:
        redis = await get_redis()
        await redis.setex(key, settings.query_cache_ttl_seconds, json.dumps(result, default=str))
        logger.info("Cached query result in Redis (TTL=%ds)", settings.query_cache_ttl_seconds)
        return
    except Exception as e:
        logger.warning("Redis cache set failed, falling back to local: %s", e)

    _set_local(key, result)
    logger.info("Cached query result locally (TTL=%ds)", settings.query_cache_ttl_seconds)


async def cache_delete(question: str, datasource_id: str) -> None:
    """Invalidate cached query result (e.g., after schema change)."""
    key = _cache_key(question, datasource_id)

    try:
        redis = await get_redis()
        await redis.delete(key)
        return
    except Exception as e:
        logger.warning("Redis cache delete failed: %s", e)

    _local_cache.pop(key, None)


def query_cache(question: str, datasource_id: str) -> dict | None:
    """Synchronous wrapper for cache_get (for non-async callers)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — create a new task
            return asyncio.ensure_future(cache_get(question, datasource_id))
        return loop.run_until_complete(cache_get(question, datasource_id))
    except Exception:
        return _get_local(_cache_key(question, datasource_id))


def query_cache_set(question: str, datasource_id: str, result: dict, ttl: int = 300) -> None:
    """Synchronous wrapper for cache_set (for non-async callers)."""
    import asyncio
    key = _cache_key(question, datasource_id)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.ensure_future(_async_cache_set_with_ttl(key, result, ttl))
        loop.run_until_complete(_async_cache_set_with_ttl(key, result, ttl))
    except Exception:
        _local_cache[key] = (time.time() + ttl, result)


async def _async_cache_set_with_ttl(key: str, result: dict, ttl: int) -> None:
    """Cache result with a specific TTL."""
    if not result.get("success") or not result.get("rows"):
        return
    try:
        redis = await get_redis()
        await redis.setex(key, ttl, json.dumps(result, default=str))
    except Exception as e:
        logger.warning("Redis cache set (ttl=%d) failed: %s", ttl, e)
        _local_cache[key] = (time.time() + ttl, result)
