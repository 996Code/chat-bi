"""Query result cache with Redis. No local fallback."""
import hashlib
import json
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)


def _cache_key(question: str, datasource_id: str, tenant_id: str = "") -> str:
    raw = f"{tenant_id}:{question.strip().lower()}:{datasource_id}"
    return f"query:{hashlib.sha256(raw.encode()).hexdigest()}"


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
    except Exception as e:
        logger.warning("Redis cache set failed: %s", e)


async def cache_delete(question: str, datasource_id: str, tenant_id: str = "") -> None:
    key = _cache_key(question, datasource_id, tenant_id)
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as e:
        logger.warning("Redis cache delete failed: %s", e)
