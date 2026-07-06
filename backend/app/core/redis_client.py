"""
ChatBI v2 — Redis Client

对标: v1 redis_client.py — 统一 Redis 连接管理
v1 经验教训 #39: 连接失败需 WARNING 日志 + 重连机制
M5: 安全相关功能降级 → ERROR 日志; 定时健康检查持续监控
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None
# M5: 连续失败计数 (定时健康检查用, 3 次 → ERROR)
_redis_fail_count: int = 0


async def get_redis(critical: bool = False) -> Optional[aioredis.Redis]:
    """Get or create the async Redis connection.

    Returns None if Redis is unavailable (caller must handle gracefully).

    Args:
        critical: True 时安全相关功能 (限流/登录锁定) 降级打 ERROR 而非 WARNING。
                  对标 SEC-005: 安全功能降级必须告警, 不能静默放行。
    """
    global _redis

    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            log_msg = "Redis connection lost, attempting reconnect..."
            if critical:
                logger.error("⚠️ %s (安全功能降级)", log_msg)
            else:
                logger.warning(log_msg)
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
        global _redis_fail_count
        _redis_fail_count = 0  # 连接成功 → 重置失败计数
        logger.info("Redis connected: %s", _sanitize_url(settings.redis_url))
        return _redis
    except Exception as e:
        msg = "Redis unavailable: %s (degraded mode)"
        if critical:
            logger.error("⚠️ " + msg, e)
        else:
            logger.warning(msg, e)
        _redis = None
        return None


async def check_redis_health() -> bool:
    """M5: 定时健康检查 (scheduler 每 60s 调一次)。

    连续失败 3 次 → ERROR 告警 (对标 SEC-005: 安全功能持续降级必须告警)。
    成功 → 重置计数 + INFO 恢复日志。
    """
    global _redis_fail_count
    conn = await get_redis()
    if conn is not None:
        if _redis_fail_count > 0:
            logger.info("Redis health check recovered (after %d failures)", _redis_fail_count)
        _redis_fail_count = 0
        return True

    _redis_fail_count += 1
    if _redis_fail_count >= 3:
        logger.error(
            "⚠️ Redis 连续不可达 %d 次 — 安全相关功能 (限流/登录锁定) 降级运行",
            _redis_fail_count,
        )
    else:
        logger.warning("Redis health check failed (count=%d)", _redis_fail_count)
    return False


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
