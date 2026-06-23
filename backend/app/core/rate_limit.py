"""
限流器 (slowapi) — 查询/登录频率限制

对标:
  - config.rate_limit_queries_per_minute (30/min)
  - config.rate_limit_login_per_minute (5/min)
  - 防滥用: Agent LLM 调用成本高, 登录防爆破

设计:
  - slowapi Limiter 单例, key 用客户端 IP (slowapi 标准 get_remote_address)
  - 在 main.py 注册中间件 + 异常处理
  - 端点用 @limiter.limit() 装饰
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """获取限流器单例 (按 IP 限流)。"""
    global _limiter
    if _limiter is not None:
        return _limiter
    _limiter = Limiter(key_func=get_remote_address, default_limits=[])
    return _limiter
