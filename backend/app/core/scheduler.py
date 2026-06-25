"""
调度器基础设施 — APScheduler AsyncIOScheduler 单例 (DSO-02/04, PERF-03 清理 的公共底座)

设计:
  - 全局单例 AsyncIOScheduler (仿 get_redis/get_embedder 模式)
  - 在 main.py lifespan startup 启动, shutdown 关闭
  - 提供 add_interval_job 注册定时任务
  - 启动失败 WARNING 降级 (fail-closed, 不阻塞应用启动, 对标经验教训#39)

对标: V1 用裸 asyncio loop, V2 用 APScheduler (CRON/间隔/持久化能力更全)
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_scheduler: Any | None = None  # AsyncIOScheduler 实例 (Any 避免运行时 import 失败)


def get_scheduler() -> Any | None:
    """获取全局调度器单例 (未启动返回 None)。"""
    return _scheduler


async def start_scheduler() -> None:
    """启动调度器 (lifespan startup 调用)。

    注册所有定时任务, 然后启动。失败 WARNING 降级 (不阻塞应用)。
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
        logger.warning("APScheduler 未安装, 定时任务不可用 (健康检查/元数据刷新/任务清理将不执行)")
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
    """注册所有定时任务 (延迟 import 避免循环依赖)。"""
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

    # 3. 异步任务清理 (PERF-03, 每小时清理过期已完成任务)
    try:
        scheduler.add_job(
            _run_async_task_cleanup,
            trigger="interval",
            hours=1,
            id="async_task_cleanup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("定时任务已注册: 异步任务清理 (每小时)")
    except Exception as e:
        logger.warning("注册异步任务清理失败: %s", e)


def shutdown_scheduler() -> None:
    """关闭调度器 (lifespan shutdown 调用)。"""
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
    """运行时注册间隔任务 (供需要动态注册的场景用)。"""
    if _scheduler is None:
        logger.warning("调度器未启动, 无法注册任务: %s", job_id)
        return
    _scheduler.add_job(
        func, trigger="interval", seconds=seconds,
        id=job_id, replace_existing=True, max_instances=1, coalesce=True,
    )


# ── 定时任务的实际执行函数 (延迟 import 避免循环依赖) ──────────

def _get_settings():
    from app.core.config import get_settings
    return get_settings()


async def _new_db_session():
    """为定时任务创建独立的 db session (不共享请求 session)。"""
    from app.db.session import get_async_session_factory
    factory = get_async_session_factory()
    return factory()


async def _run_datasource_health_check() -> None:
    """定时执行: 全量数据源健康检查 (DSO-02)。"""
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
    """定时执行: 元数据自动刷新 (DSO-04)。"""
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


async def _run_async_task_cleanup() -> None:
    """定时执行: 清理过期已完成异步任务 (PERF-03)。"""
    try:
        from app.api.async_query import cleanup_expired_tasks
        session = await _new_db_session()
        try:
            await cleanup_expired_tasks(session)
            await session.commit()
        finally:
            await session.close()
    except Exception as e:
        logger.warning("定时任务清理执行失败: %s", e)
