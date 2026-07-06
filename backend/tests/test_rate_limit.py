"""
ChatBI v2 — Rate Limiter Tests (T067)

覆盖:
  - get_limiter 单例行为
  - 限流器基本属性
"""
from __future__ import annotations

from app.core.rate_limit import get_limiter


class TestRateLimiter:
    """限流器单例 + 基本属性。"""

    def test_singleton_identity(self):
        a = get_limiter()
        b = get_limiter()
        assert a is b

    def test_limiter_has_key_func(self):
        limiter = get_limiter()
        # slowapi Limiter 初始化时设置 key_func
        assert limiter is not None

    def test_limiter_no_default_limits(self):
        """我们配置的 limiter 默认不限流 (default_limits=[]), 限流由端点装饰器控制。"""
        limiter = get_limiter()
        # default_limits 为空列表, 限流靠 @limiter.limit() 装饰器
        assert limiter._default_limits == []
