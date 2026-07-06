"""
DSO-02: 数据源健康检查

对标 V1 设计:
  - 定时 (5 分钟) 检查所有活跃数据源连通性 (TCP + 认证 + SELECT 1)
  - 连续失败 N 次 → 标记 error (is_active=False), 暂停查询
  - 健康检查恢复 → is_active=True + 重建连接池
  - 记审计 + WARNING

机制:
  - ping_datasource: 复用 DataSourceEnginePool, engine.connect() + SELECT 1, 超时 5s
  - check_all_datasources_health: 定时任务入口, 遍历 active 数据源
  - 连续失败计数用内存 dict (重启丢失, 但 error 状态持久化在 is_active)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import DataSource

logger = logging.getLogger(__name__)

# 连续失败计数 (内存, 单进程; key=data_source_id, value=fail_count)
# 重启后归零 — 已知限制, 但 error 状态(is_active=False)是持久的
_health_fail_counts: dict[str, int] = {}

# 探活超时 (秒)
_PING_TIMEOUT = 5


@dataclass
class PingResult:
    """健康检查结果。"""
    ok: bool
    latency_ms: int = 0
    error: str | None = None


def _ping_sync(url: str, datasource_id: str) -> PingResult:
    """同步 ping: 从连接池取 engine, connect + SELECT 1。

    在 to_thread 里跑 (同 sql_executor 范式)。
    """
    from sqlalchemy import text
    from app.services.datasource_engine import get_engine_pool

    t0 = time.monotonic()
    try:
        pool = get_engine_pool()
        engine = pool.get_or_create(datasource_id, url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return PingResult(ok=True, latency_ms=round((time.monotonic() - t0) * 1000))
    except Exception as e:
        return PingResult(
            ok=False,
            latency_ms=round((time.monotonic() - t0) * 1000),
            error=str(e)[:200],
        )


async def ping_datasource(ds: DataSource) -> PingResult:
    """ping 单个数据源 (异步, 超时保护)。

    复用 datasource_to_url 解密密码 + DataSourceEnginePool 连接池。
    """
    from app.services.datasource_engine import datasource_to_url
    url = datasource_to_url(ds)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_ping_sync, url, ds.id),
            timeout=_PING_TIMEOUT,
        )
        return result
    except asyncio.TimeoutError:
        return PingResult(ok=False, error=f"健康检查超时 ({_PING_TIMEOUT}s)")


async def check_all_datasources_health(db_session, tenant_id: str | None = None) -> dict:
    """定时任务: 检查所有数据源健康状态。

    - 遍历所有数据源 (含 is_active=False 的, 给它们恢复机会)
    - ping 成功 + 当前 is_active=False → 恢复 + dispose 旧池重建
    - ping 失败 → 累计连续失败; 达 max_failures → 标记 is_active=False
    - 返回汇总 dict (供手动触发端点展示)

    M6: tenant_id 限定本租户数据源 (定时任务不传=全量, API 调用传=租户隔离)
    不抛异常 (定时任务容错), 失败只 WARNING。
    """
    from app.core.config import get_settings
    settings = get_settings()
    max_failures = settings.datasource_health_check_max_failures

    # 查所有数据源 (不限于 active, 让 error 的有恢复机会)
    # M6: 加 tenant_id 过滤 (跨租户隔离, admin 只能查本租户数据源)
    query = select(DataSource)
    if tenant_id:
        query = query.where(DataSource.tenant_id == tenant_id)
    result = await db_session.execute(query)
    datasources = result.scalars().all()

    summary = {"checked": 0, "healthy": 0, "unhealthy": 0, "recovered": 0, "newly_error": 0}

    # 并发 ping (IO 密集型, gather 比串行快 N 倍; 中-6 修复)
    # 带并发上限避免数据源过多时压力过大
    semaphore = asyncio.Semaphore(10)  # 最多 10 个并发 ping

    async def _ping_with_limit(ds):
        async with semaphore:
            return ds, await ping_datasource(ds)

    ping_results = await asyncio.gather(
        *(_ping_with_limit(ds) for ds in datasources),
        return_exceptions=True,
    )

    # 串行更新状态 (避免 is_active/计数竞态)
    for result in ping_results:
        if isinstance(result, Exception):
            logger.warning("ping 任务异常: %s", result)
            summary["checked"] += 1
            summary["unhealthy"] += 1
            continue
        ds, ping = result
        summary["checked"] += 1
        if ping.ok:
            summary["healthy"] += 1
            _health_fail_counts.pop(ds.id, None)  # 成功清零
            # 恢复: 之前是 error, 现在 ping 通了
            if not ds.is_active:
                ds.is_active = True
                summary["recovered"] += 1
                # 不 dispose 连接池 (高-3 修复: ping 通说明池内连接可用,
                # 无条件 dispose 会中断正在执行的用户查询)
                logger.info("数据源 %s (%s) 健康恢复", ds.id, ds.name)
                await _audit_health(db_session, ds, "recovered", ping.latency_ms)
        else:
            summary["unhealthy"] += 1
            count = _health_fail_counts.get(ds.id, 0) + 1
            _health_fail_counts[ds.id] = count
            logger.warning(
                "数据源 %s (%s) 健康检查失败 (%d/%d): %s",
                ds.id, ds.name, count, max_failures, ping.error,
            )
            # 达阈值 → 标记 error (仅首次标记时记审计)
            if count >= max_failures and ds.is_active:
                ds.is_active = False
                summary["newly_error"] += 1
                logger.warning("数据源 %s (%s) 连续失败 %d 次, 标记为 error", ds.id, ds.name, count)
                await _audit_health(db_session, ds, "unhealthy", None, ping.error)

    return summary


async def _audit_health(db_session, ds: DataSource, status: str, latency_ms: int | None, error: str | None = None) -> None:
    """健康检查结果记审计 (不抛异常)。"""
    try:
        from app.core.auth import write_audit_log
        await write_audit_log(
            db_session, tenant_id=ds.tenant_id, user_id=None,
            resource_type="data_source", action="health_check",
            status="success" if status == "recovered" else "fail",
            resource_id=ds.id,
            detail={"latency_ms": latency_ms, "error": error} if error else {"latency_ms": latency_ms},
            error_message=error,
        )
    except Exception as e:
        logger.warning("健康检查审计失败 (不阻塞): %s", e)
