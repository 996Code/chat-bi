"""Singleton async Redis connection pool."""
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return a shared async Redis connection (singleton pool).

    Raises ConnectionError if redis_url is not configured.
    Callers should catch exceptions and fall back to in-memory.
    """
    global _redis
    if _redis is None:
        if not settings.redis_url:
            raise ConnectionError("Redis is not configured (redis_url is empty)")
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.db_pool_size * 2,
            socket_timeout=5,
            socket_connect_timeout=3,
            retry_on_timeout=True,
        )
        logger.info("Redis connection pool created: %s", settings.redis_url)
    return _redis


async def close_redis():
    """Close the shared Redis connection."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed")


async def redis_available() -> bool:
    """Check if Redis is reachable."""
    try:
        client = await get_redis()
        return await client.ping()
    except Exception:
        return False
