"""Rate limiting middleware using sliding window."""
import time
from collections import defaultdict
from fastapi import Request, HTTPException, status

from app.core.logging import get_logger

logger = get_logger(__name__)

# In-memory rate limiter (production should use Redis)
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


def check_rate_limit(key: str, config: RateLimitConfig) -> bool:
    """检查是否超过限流阈值。返回 True 表示被限制。"""
    _clean_expired(key, config.window_seconds)
    timestamps = _rate_limits[key]
    if len(timestamps) >= config.max_requests:
        return True
    timestamps.append(time.time())
    return False


async def rate_limit_middleware(request: Request, call_next):
    """FastAPI middleware：按 IP 限流，60 次/分钟。"""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path

    # Skip rate limit for health checks
    if path in ("/health", "/docs", "/openapi.json"):
        return await call_next(request)

    # Different limits for different endpoints
    if path.startswith("/api/v1/auth/login"):
        config = RateLimitConfig(max_requests=10, window_seconds=60)
    elif path.startswith("/api/v1/query"):
        config = RateLimitConfig(max_requests=30, window_seconds=60)
    else:
        config = RateLimitConfig(max_requests=60, window_seconds=60)

    key = f"{client_ip}:{path}"
    if check_rate_limit(key, config):
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
