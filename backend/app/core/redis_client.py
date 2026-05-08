"""
ChatBI Redis 客户端 — 异步连接 + 单例模式

本文件管理 Redis 连接的生命周期，提供全局唯一的异步 Redis 客户端。

核心概念：
1. 异步 Redis：使用 redis.asyncio（原 aioredis），所有操作都是协程，不阻塞事件循环
2. 单例模式：模块级变量 _redis 保存唯一连接实例，整个应用共享
3. 连接池：from_url 自动创建连接池，管理多个 TCP 连接，避免频繁创建/销毁
4. 惰性初始化：首次调用 get_redis() 时才创建连接，而非模块导入时

Redis 在 ChatBI 中的三大用途：
- 查询缓存：缓存 SQL 查询结果，相同问题直接返回缓存
- 限流计数：记录每个 IP 的请求次数，超限则拒绝
- 登录锁定：记录登录失败次数，超限则临时锁定账号

关联文件：
- app/core/config.py — 提供 redis_url、db_pool_size 等配置
- app/main.py — 关闭时调用 close_redis() 释放连接
- app/core/rate_limiter.py — 使用 Redis 做限流计数
- app/services/cache_service.py — 使用 Redis 做查询缓存
"""

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---- 模块级单例 ----
# _redis 保存全局唯一的 Redis 客户端实例
# 下划线前缀表示"模块内部变量"，不希望外部直接访问（Python 约定，非强制）
# 类型注解 aioredis.Redis | None 是 Python 3.10+ 的联合类型语法，等价于 Optional[aioredis.Redis]
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    获取 Redis 客户端实例（惰性单例）

    返回：aioredis.Redis 异步客户端实例

    工作原理：
    1. 首次调用时 _redis 为 None，创建连接并保存
    2. 后续调用直接返回已创建的实例
    3. 整个应用生命周期中只有一个 Redis 连接池

    惰性初始化的优势：
    - 模块导入时不需要 Redis 可用（测试环境可能没有 Redis）
    - 只在实际需要时才创建连接，节省资源
    - 避免循环导入问题（如果模块导入时就连接，可能其他模块还没初始化）

    global 关键字：
    - Python 中函数内赋值会创建局部变量，global 声明告诉解释器
      _redis 是模块级变量，不是新建的局部变量
    - 如果不加 global，_redis = ... 会创建一个同名局部变量，模块级的 _redis 不会被修改

    连接参数说明：
    - decode_responses=True：自动将 Redis 返回的 bytes 解码为 str
      不设置的话，所有返回值都是 bytes 类型，需要手动 .decode()
    - max_connections：连接池最大连接数，设为 db_pool_size * 2
    - socket_timeout=5：读写操作超时 5 秒
    - socket_connect_timeout=3：建立连接超时 3 秒
    - retry_on_timeout=True：超时后自动重试，提高可靠性
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
    """
    关闭 Redis 连接，释放所有资源

    在应用关闭时由 lifespan 调用（见 app/main.py）

    工作原理：
    1. 调用 aclose() 关闭连接池中所有连接
    2. 将 _redis 重置为 None，允许后续重新初始化（主要用于测试）

    为什么需要显式关闭：
    - Redis 连接是 TCP 长连接，不关闭会占用服务端资源
    - Python 进程退出时虽然会自动关闭，但优雅关闭更可靠
    - 测试场景中可能需要多次创建/关闭连接
    """
    global _redis
    if _redis:
        await _redis.aclose()  # 异步关闭，释放连接池中所有连接
        _redis = None          # 重置为 None，允许重新初始化
        logger.info("Redis connection closed")


async def redis_available() -> bool:
    """
    检查 Redis 是否可用

    返回：True = 可连接，False = 不可连接

    用途：启动时或运行时检测 Redis 状态，不可用时降级为内存缓存
    ping() 是 Redis 的心跳命令，成功返回 True，失败抛异常
    """
    try:
        client = await get_redis()
        return await client.ping()
    except Exception:
        return False
