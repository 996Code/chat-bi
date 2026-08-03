"""
ChatBI v2 — Redis Client

对标: v1 redis_client.py — 统一 Redis 连接管理
v1 经验教训 #39: 连接失败需 WARNING 日志 + 重连机制
M5: 安全相关功能降级 → ERROR 日志; 定时健康检查持续监控

架构角色:
  - 全局单例 async Redis 连接, 通过 get_redis() 获取
  - 自动重连: 连接断开后下次调用自动尝试重建
  - 降级模式: Redis 不可用时, 安全相关功能 (限流/登录锁定) 降级运行
  - 健康检查: check_redis_health() 供 scheduler 定时调用, 连续失败 3 次 → ERROR

关键设计:
  - critical 参数: 区分安全功能和非安全功能的降级告警级别
  - _redis_fail_count: 连续失败计数器, 用于判断是否已持续不可达
  - 连接泄漏防护: 每次重连前 close 旧连接, 防止 fd 泄漏
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

    重连机制:
      1. 检查已有连接, 发送 PING 验证可用性
      2. PING 失败 → 关闭旧连接, 创建新连接
      3. 新连接也失败 → 返回 None, 调用方自行降级

    注意: 返回 None 时, 调用方应提供降级方案 (如本地缓存), 而非直接报错。
    """
    global _redis

    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            # 连接可用但 PING 失败 → 连接已断, 需要重建
            # 不区分具体的异常类型: 网络超时/连接重置/服务端断开都走重连
            log_msg = "Redis connection lost, attempting reconnect..."
            if critical:
                logger.error("⚠️ %s (安全功能降级)", log_msg)
            else:
                logger.warning(log_msg)
            try:
                await _redis.close()
            except Exception:
                # close 失败不影响重建, 旧连接会被 GC 回收
                pass
            _redis = None

    settings = get_settings()
    try:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            # socket_connect_timeout=3: 3 秒超时, 避免长时间阻塞
            socket_connect_timeout=3,
            # socket_keepalive=True: 启用 TCP keepalive, 及时发现网络断开
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

    为什么是 3 次:
      - 1 次失败可能是网络抖动, 不应立即告警
      - 3 次连续失败 (约 3 分钟) 说明问题持续存在, 需要运维介入

    恢复通知: 恢复后记录 INFO 日志, 标明之前连续失败次数, 方便排查。
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
    """Strip password from Redis URL for safe logging.

    脱敏规则: scheme://[user]:[password]@host → scheme://***@host
    正则匹配: 从 :// 后到第一个 @ 之间的 user:pass 部分替换为 ***
    如果 URL 中不含密码 (如 unix socket), 正则不匹配, 原样返回。
    """
    return re.sub(r"://[^@]*@", "://***@", url)


async def close_redis() -> None:
    """Close the Redis connection gracefully.

    在应用 shutdown 时调用, 确保所有待处理命令完成后再关闭。
    如果连接已为 None, 静默跳过。
    """
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        logger.info("Redis connection closed")
