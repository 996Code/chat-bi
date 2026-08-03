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

为什么用 slowapi:
  - 内建 FastAPI 支持, 无需额外中间件适配
  - 支持 Redis 存储 (生产环境, 对标 redis_client 降级模式)
  - 默认内存存储 (开发/测试环境, 无需 Redis)

限流策略:
  - 查询: 30 次/分钟/IP (Agent LLM 调用成本高, 防止滥用)
  - 登录: 5 次/分钟/IP (防止暴力破解)
  - 刷新 token: 10 次/分钟/IP (防止 refresh token 滥用)

注意: 当 Redis 不可用时, slowapi 自动降级到内存存储 (不安全, 但不会误杀)。
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """获取限流器单例 (按 IP 限流)。

    单例模式: 首次调用时创建, 后续复用。
    默认不设全局限制 (default_limits=[]), 由各端点通过 @limiter.limit() 单独设置。

    注意: 在多 worker 模式下 (如 gunicorn), 内存存储的限流器不准确。
    生产环境应使用 Redis 存储, 通过 slowapi 的 RedisStorage 配置。
    当前实现使用内存存储, 适合单进程开发和测试。
    """
    global _limiter
    if _limiter is not None:
        return _limiter
    _limiter = Limiter(key_func=get_remote_address, default_limits=[])
    return _limiter
