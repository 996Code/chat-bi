"""Periodic task scheduler for metadata auto-refresh."""
import asyncio
import json
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_scheduler_task: asyncio.Task | None = None


async def _auto_refresh_loop():
    """Background loop that triggers metadata auto-refresh at configured intervals."""
    from app.core.redis_client import get_redis
    from app.db.session import async_session_factory
    from sqlalchemy import select
    from app.db.models import DataSource

    while True:
        try:
            redis = await get_redis()
            if not redis:
                await asyncio.sleep(60)
                continue

            raw = await redis.get("metadata_auto_refresh:config")
            config = json.loads(raw) if raw else {}
            enabled = config.get("enabled", settings.metadata_auto_refresh_enabled)
            interval = config.get("interval_minutes", settings.metadata_auto_refresh_interval_minutes)

            if enabled:
                logger.info("Auto-refresh: scheduling metadata refresh (interval=%dm)", interval)
                # Trigger via API endpoint logic
                # We import here to avoid circular imports
                from app.api.data_model import AUTO_REFRESH_PREFIX
                from app.db.session import async_session_factory

                async with async_session_factory() as session:
                    # Get datasources
                    if config.get("datasources"):
                        from sqlalchemy import text
                        ds_ids = config["datasources"]
                    else:
                        result = await session.execute(
                            select(DataSource).where(
                                DataSource.is_active == True,
                            )
                        )
                        ds_list = result.scalars().all()
                        ds_ids = [str(ds.id) for ds in ds_list]

                    for ds in ds_list:
                        logger.info("Auto-refresh: triggering sync for datasource %s (%s)", ds.id, ds.name)
                        try:
                            from app.api.data_model import _run_sync_background, _create_sync_task
                            tenant_id = str(ds.tenant_id)
                            task_id = f"auto-{ds.id}-{asyncio.get_event_loop().time():.0f}"
                            await _create_sync_task(task_id, str(ds.id), tenant_id, "incremental")
                            asyncio.create_task(_run_sync_background(task_id, str(ds.id), tenant_id, "incremental"))
                        except Exception as e:
                            logger.error("Auto-refresh: failed to sync datasource %s: %s", ds.id, e)

            await asyncio.sleep(interval * 60)
        except asyncio.CancelledError:
            logger.info("Auto-refresh scheduler cancelled")
            break
        except Exception as e:
            logger.error("Auto-refresh loop error: %s", e)
            await asyncio.sleep(60)


async def start_scheduler():
    """Start the background scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        logger.warning("Scheduler already running")
        return

    logger.info("Starting metadata auto-refresh scheduler")
    _scheduler_task = asyncio.create_task(_auto_refresh_loop())


async def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Scheduler stopped")
