"""Rate limiting middleware using Redis sliding window with in-memory fallback."""
import time
from collections import defaultdict
from fastapi import Request, HTTPException, status

from app.core.logging import get_logger
from app.core.redis_client import get_redis, redis_available

logger = get_logger(__name__)

# In-memory fallback (sliding window dict)
_rate_limits: dict[str, list[float]] = defaultdict(list)


class RateLimitConfig:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds


def _clean_expired(key: str, window: float) -> None:
    now = time.time()
    cutoff = now - window
    timestamps = _rate_limits[key]
    _rate_limits[key] = [t for t in timestamps if t > cutoff]


def check_rate_limit_local(key: str, config: RateLimitConfig) -> bool:
    """Check rate limit using in-memory sliding window. Returns True if limited."""
    _clean_expired(key, config.window_seconds)
    timestamps = _rate_limits[key]
    if len(timestamps) >= config.max_requests:
        return True
    timestamps.append(time.time())
    return False


async def check_rate_limit(key: str, max_requests: int, window: int) -> bool:
    """
    Check rate limit using Redis sliding window.
    Returns True if the request should be rate-limited.
    Falls back to in-memory implementation if Redis is unavailable.
    """
    try:
        redis = await get_redis()
        now = time.time()
        cutoff = now - window

        # Use a sorted set: score = timestamp, member = unique timestamp
        pipe = redis.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, cutoff)
        # Count remaining entries
        pipe.zcard(key)
        pipe.execute()

        # Re-count after cleanup
        count = await redis.zcard(key)
        if count >= max_requests:
            return True

        # Add current request (member must be unique)
        await redis.zadd(key, {f"{now}:{time.monotonic_ns()}": now})
        # Set expiry on the key itself
        await redis.expire(key, window + 10)
        return False
    except Exception as e:
        logger.warning("Redis rate limit failed, falling back to local: %s", e)
        return check_rate_limit_local(key, RateLimitConfig(max_requests=max_requests, window_seconds=window))


async def rate_limit_middleware(request: Request, call_next):
    """FastAPI middleware: per-IP sliding window rate limiting."""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path

    # Skip rate limit for health checks
    if path in ("/health", "/docs", "/openapi.json"):
        return await call_next(request)

    # Different limits for different endpoints
    if "/auth/login" in path:
        max_req = 10
        window_sec = 60
    elif "/query" in path:
        max_req = 30
        window_sec = 60
    else:
        max_req = 60
        window_sec = 60

    key = f"rl:{client_ip}:{path}"
    if await check_rate_limit(key, max_req, window_sec):
        logger.warning("Rate limit exceeded for %s on %s", client_ip, path)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后重试",
                "details": None,
            },
        )

    response = await call_next(request)
    return response
