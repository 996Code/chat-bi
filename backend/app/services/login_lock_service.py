"""登录锁定服务：基于 Redis 的登录失败计数和锁定。支持多实例部署。
Redis 不可用时自动降级到内存模式。
"""
import time
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 5
LOCK_DURATION = 900  # 15 minutes
LOCK_KEY_PREFIX = "login_lock:"

# In-memory fallback when Redis is unavailable
_fallback: dict[str, dict] = {}


def _lock_key(email: str) -> str:
    return f"{LOCK_KEY_PREFIX}{email.lower()}"


async def _get_redis():
    try:
        from app.core.redis_client import get_redis
        return await get_redis()
    except Exception:
        return None


async def check_lock(email: str) -> bool:
    key = _lock_key(email)
    redis = await _get_redis()
    if redis:
        try:
            locked_until = await redis.get(f"{key}:locked_until")
            if locked_until:
                if time.time() < float(locked_until):
                    return True
                # Lock expired — clean up
                await redis.delete(f"{key}:locked_until")
                await redis.delete(f"{key}:count")
        except Exception:
            pass
    else:
        # Memory fallback
        entry = _fallback.get(key)
        if entry:
            locked_until = entry.get("locked_until")
            if locked_until and time.time() < locked_until:
                return True
            if locked_until:
                _fallback.pop(key, None)
    return False


async def record_failure(email: str) -> None:
    key = _lock_key(email)
    redis = await _get_redis()
    if redis:
        try:
            count = await redis.incr(f"{key}:count")
            if count >= MAX_ATTEMPTS:
                locked_until = time.time() + LOCK_DURATION
                await redis.set(f"{key}:locked_until", str(locked_until), ex=LOCK_DURATION)
                logger.warning("Account locked: %s after %d failed attempts", email.lower(), count)
        except Exception:
            logger.error("Failed to record login failure in Redis", exc_info=True)
    else:
        # Memory fallback
        entry = _fallback.setdefault(key, {"count": 0, "locked_until": None})
        entry["count"] += 1
        if entry["count"] >= MAX_ATTEMPTS:
            entry["locked_until"] = time.time() + LOCK_DURATION
            logger.warning("Account locked (memory): %s after %d failed attempts", email.lower(), entry["count"])


async def reset(email: str) -> None:
    key = _lock_key(email)
    redis = await _get_redis()
    if redis:
        try:
            await redis.delete(f"{key}:count")
            await redis.delete(f"{key}:locked_until")
        except Exception:
            pass
    else:
        _fallback.pop(key, None)
