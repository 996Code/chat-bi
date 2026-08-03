"""
调度器基础设施 — APScheduler AsyncIOScheduler 单例 (DSO-02/04 的公共底座)

设计:
  - 全局单例 AsyncIOScheduler (仿 get_redis/get_embedder 模式)
  - 在 main.py lifespan startup 启动, shutdown 关闭
  - 提供 add_interval_job 注册定时任务
  - 启动失败 WARNING 降级 (fail-closed, 不阻塞应用启动, 对标经验教训#39)

对标: V1 用裸 asyncio loop, V2 用 APScheduler (CRON/间隔/持久化能力更全)

注册的定时任务:
  1. 数据源健康检查 (DSO-02): 定期检查所有数据源连接状态
  2. 元数据自动刷新 (DSO-04): 检测并刷新数据源元数据
  3. Redis 健康检查 (M5): 持续监控 Redis 可用性, 连续失败告警

关键设计:
  - 延迟 import: 所有任务执行函数都在实际执行时 import, 避免循环依赖
  - 独立 db session: 定时任务使用独立 session, 不共享请求 session
  - max_instances=1: 防止任务重叠执行 (如健康检查耗时超过间隔)
  - coalesce=True: 错过多次执行时只执行一次, 不积压
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_scheduler: Any | None = None  # AsyncIOScheduler 实例 (Any 避免运行时 import 失败)


def get_scheduler() -> Any | None:
    """获取全局调度器单例 (未启动返回 None)。

    调用方使用前应检查返回值是否为 None。
    如果为 None, 说明调度器未启动 (scheduler_enabled=False 或启动失败)。
    """
    return _scheduler


async def start_scheduler() -> None:
    """启动调度器 (lifespan startup 调用)。

    注册所有定时任务, 然后启动。失败 WARNING 降级 (不阻塞应用)。

    启动流程:
      1. 检查 scheduler_enabled 配置
      2. 延迟 import APScheduler (避免未安装时崩溃)
      3. 创建 AsyncIOScheduler 实例
      4. 注册所有定时任务 (数据源健康检查/元数据刷新/Redis 健康检查)
      5. 启动调度器

    降级: 任何步骤失败 → WARNING 日志, 不抛异常, 不阻塞应用启动。
    """
    global _scheduler
    settings = _get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用 (scheduler_enabled=False), 跳过启动")
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("APScheduler 未安装, 定时任务不可用 (健康检查/元数据刷新将不执行)")
        return

    _scheduler = AsyncIOScheduler(logger=logger)

    # 注册定时任务 (各模块的 check/refresh 函数)
    try:
        _register_jobs(_scheduler, settings)
        _scheduler.start()
        logger.info("调度器已启动, 定时任务注册完成")
    except Exception as e:
        logger.warning("调度器启动失败, 定时任务将不可用: %s", e)
        _scheduler = None


def _register_jobs(scheduler: Any, settings: Any) -> None:
    """注册所有定时任务 (延迟 import 避免循环依赖)。

    每个任务都独立 try/except, 确保一个任务注册失败不影响其他任务。
    replace_existing=True: 重复调用时替换已有任务, 避免重复注册。
    max_instances=1: 防止任务重叠执行。
    coalesce=True: 错过多个执行时刻时只执行一次。
    """
    from datetime import timedelta

    # 1. 数据源健康检查 (DSO-02)
    try:
        scheduler.add_job(
            _run_datasource_health_check,
            trigger="interval",
            seconds=settings.datasource_health_check_interval_seconds,
            id="datasource_health_check",
            replace_existing=True,
            max_instances=1,  # 防止重叠运行
            coalesce=True,
        )
        logger.info("定时任务已注册: 数据源健康检查 (每 %ds)", settings.datasource_health_check_interval_seconds)
    except Exception as e:
        logger.warning("注册数据源健康检查任务失败: %s", e)

    # 2. 元数据自动刷新 (DSO-04)
    try:
        scheduler.add_job(
            _run_metadata_refresh,
            trigger="interval",
            hours=settings.metadata_auto_refresh_interval_hours,
            id="metadata_auto_refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("定时任务已注册: 元数据自动刷新 (每 %dh)", settings.metadata_auto_refresh_interval_hours)
    except Exception as e:
        logger.warning("注册元数据刷新任务失败: %s", e)

    # 3. Redis 健康检查 (M5: 持续监控, 连续失败 → ERROR 告警)
    try:
        scheduler.add_job(
            _run_redis_health_check,
            trigger="interval",
            seconds=60,
            id="redis_health_check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("定时任务已注册: Redis 健康检查 (每 60s)")
    except Exception as e:
        logger.warning("注册 Redis 健康检查任务失败: %s", e)


def shutdown_scheduler() -> None:
    """关闭调度器 (lifespan shutdown 调用)。

    等待当前运行的任务完成 (wait=False 则不等待)。
    在 finally 中置 None, 确保即使 close 异常也释放引用。
    """
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("调度器已关闭")
        except Exception as e:
            logger.warning("调度器关闭异常: %s", e)
        finally:
            _scheduler = None


def add_interval_job(func: Callable, seconds: int, job_id: str) -> None:
    """运行时注册间隔任务 (供需要动态注册的场景用)。

    如果调度器未启动, 记录 WARNING 并跳过 (不抛异常)。
    """
    if _scheduler is None:
        logger.warning("调度器未启动, 无法注册任务: %s", job_id)
        return
    _scheduler.add_job(
        func, trigger="interval", seconds=seconds,
        id=job_id, replace_existing=True, max_instances=1, coalesce=True,
    )


# ── 定时任务的实际执行函数 (延迟 import 避免循环依赖) ──────────

def _get_settings():
    """延迟获取配置, 避免模块加载时的循环依赖。"""
    from app.core.config import get_settings
    return get_settings()


async def _new_db_session():
    """为定时任务创建独立的 db session (不共享请求 session)。

    定时任务在后台线程中运行, 没有 HTTP 请求上下文,
    因此必须创建独立的 session, 不能使用请求 scope 的 session 依赖。
    """
    from app.db.session import get_async_session_factory
    factory = get_async_session_factory()
    return factory()


async def _run_datasource_health_check() -> None:
    """定时执行: 全量数据源健康检查 (DSO-02)。

    遍历所有数据源, 检查连接是否可用。
    结果写入 audit_log 和 datasource_health 表。
    """
    try:
        from app.services.datasource_health import check_all_datasources_health
        session = await _new_db_session()
        try:
            await check_all_datasources_health(session)
            await session.commit()
        finally:
            await session.close()
    except Exception as e:
        logger.warning("定时健康检查执行失败: %s", e)


async def _run_metadata_refresh() -> None:
    """定时执行: 元数据自动刷新 (DSO-04)。

    检测数据源中的表结构变更 (新增/删除/修改字段),
    自动更新 semantic_models 中的元数据缓存。
    """
    try:
        from app.services.metadata_refresher import detect_and_refresh_metadata
        session = await _new_db_session()
        try:
            await detect_and_refresh_metadata(session)
            await session.commit()
        finally:
            await session.close()
    except Exception as e:
        logger.warning("定时元数据刷新执行失败: %s", e)


async def _run_redis_health_check() -> None:
    """M5: 定时执行: Redis 健康检查 (持续监控, 对标 SEC-005)。

    连续失败 3 次 → ERROR 告警 (由 check_redis_health 内部实现)。
    成功 → 重置计数器, 记录恢复日志。
    """
    try:
        from app.core.redis_client import check_redis_health
        await check_redis_health()
    except Exception as e:
        logger.warning("Redis 健康检查执行失败: %s", e)
