"""
ChatBI v2 — Redis Client

对标: v1 redis_client.py — 统一 Redis 连接管理
v1 经验教训 #39: 连接失败需 WARNING 日志 + 重连机制
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> Optional[aioredis.Redis]:
    """Get or create the async Redis connection.

    Returns None if Redis is unavailable (caller must handle gracefully).
    Logs WARNING on first connection failure.
    """
    global _redis

    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            logger.warning("Redis connection lost, attempting reconnect...")
            try:
                await _redis.close()
            except Exception:
                pass
            _redis = None

    settings = get_settings()
    try:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
            socket_keepalive=True,
        )
        await _redis.ping()
        logger.info("Redis connected: %s", _sanitize_url(settings.redis_url))
        return _redis
    except Exception as e:
        logger.warning("Redis unavailable: %s (degraded mode)", e)
        _redis = None
        return None


def _sanitize_url(url: str) -> str:
    """Strip password from Redis URL for safe logging."""
    return re.sub(r"://[^@]*@", "://***@", url)


async def close_redis() -> None:
    """Close the Redis connection gracefully."""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        logger.info("Redis connection closed")