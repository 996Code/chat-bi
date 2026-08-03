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

设计决策:
  - 为什么用内存计数而非 DB: 连续失败计数是运行时状态, 单进程场景下
    内存计数足够, 避免频繁写 DB。重启后计数归零, error 数据源仍被标记为
    is_active=False, 需要手动或下次健康检查通过后恢复。
  - 为什么 is_active=False 的数据源也检查: 给它们恢复机会, 避免永久死锁。
    只有健康检查通过才能恢复 is_active=True, 管理员手动改也行。
  - 并发 ping 用 Semaphore 限制: 避免 100+ 数据源同时 ping 造成网络风暴。
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
# 5 秒超时: 大多数业务库在 1-2 秒内响应, 5 秒足够区分连接失败和慢查询。
# 过长超时会导致健康检查任务阻塞, 影响定时任务调度。
_PING_TIMEOUT = 5


@dataclass
class PingResult:
    """健康检查结果。

    在 to_thread 中构造, 返回给 async 调用方。
    latency_ms 用于监控和审计, 可追踪慢查询数据源。
    error 截断到 200 字符: 避免存储完整错误栈 (可能包含密码/连接串)。
    """
    ok: bool
    latency_ms: int = 0
    error: str | None = None


def _ping_sync(url: str, datasource_id: str) -> PingResult:
    """同步 ping: 从连接池取 engine, connect + SELECT 1。

    在 to_thread 里跑 (同 sql_executor 范式)。

    为什么用 SELECT 1 而非更复杂的查询:
    - SELECT 1 是最轻量的数据库往返, 不依赖任何表
    - 验证了 TCP 连通性 + 认证 + 数据库响应能力
    - 如果 SELECT 1 都失败, 更复杂的查询必然也失败

    为什么用 engine.connect() 而非 pool 的 raw_connection:
    - connect() 走完整的连接池管理 (包括 pool_pre_ping 检查)
    - 能检查连接池是否正常工作, 而不仅仅是数据库可达
    """
    from sqlalchemy import text
    from app.services.datasource_engine import get_engine_pool

    t0 = time.monotonic()
    try:
        pool = get_engine_pool()
        engine = pool.get_or_create(datasource_id, url)
        # connect() 会从连接池获取连接, 如果 pool_pre_ping=True,
        # SQLAlchemy 会先执行 SELECT 1 确认连接存活, 然后再执行我们的 SELECT 1
        # 这导致两次探测, 但保证了连接池的健康
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return PingResult(ok=True, latency_ms=round((time.monotonic() - t0) * 1000))
    except Exception as e:
        # 捕获所有异常: 连接超时, 认证失败, 数据库不可达, 连接池耗尽等
        # 不在此处区分错误类型, 交给调用方处理
        return PingResult(
            ok=False,
            latency_ms=round((time.monotonic() - t0) * 1000),
            error=str(e)[:200],  # 截断, 防止敏感信息泄露
        )


async def ping_datasource(ds: DataSource) -> PingResult:
    """ping 单个数据源 (异步, 超时保护)。

    复用 datasource_to_url 解密密码 + DataSourceEnginePool 连接池。

    超时保护: asyncio.wait_for 确保整个操作 (包括密码解密和引擎创建)
    在 _PING_TIMEOUT 秒内完成。如果超时, 返回错误结果而非抛出异常。

    注意: asyncio.to_thread 将同步的 _ping_sync 放到线程池执行,
    避免阻塞事件循环。这是 SQLAlchemy 同步引擎的标准异步包装方式。
    """
    from app.services.datasource_engine import datasource_to_url
    url = datasource_to_url(ds)
    try:
        # 使用 wait_for 确保超时, 防止数据库死连接导致健康检查卡住
        result = await asyncio.wait_for(
            asyncio.to_thread(_ping_sync, url, ds.id),
            timeout=_PING_TIMEOUT,
        )
        return result
    except asyncio.TimeoutError:
        # 超时错误单独处理, 不走到 _ping_sync 的 except 分支
        return PingResult(ok=False, error=f"健康检查超时 ({_PING_TIMEOUT}s)")


async def check_all_datasources_health(db_session, tenant_id: str | None = None) -> dict:
    """定时任务: 检查所有数据源健康状态。

    - 遍历所有数据源 (含 is_active=False 的, 给它们恢复机会)
    - ping 成功 + 当前 is_active=False → 恢复 + dispose 旧池重建
    - ping 失败 → 累计连续失败; 达 max_failures → 标记 is_active=False
    - 返回汇总 dict (供手动触发端点展示)

    M6: tenant_id 限定本租户数据源 (定时任务不传=全量, API 调用传=租户隔离)
    不抛异常 (定时任务容错), 失败只 WARNING。

    数据流:
    1. 查询所有数据源 (含 is_active=False)
    2. 并发 ping (Semaphore 限制 10 并发)
    3. 串行更新每个数据源的 is_active 和 _health_fail_counts
    4. 记审计日志

    错误处理:
    - 单个数据源 ping 超时/失败不会影响其他数据源
    - 审计日志写入失败不阻塞主流程
    - 汇总 dict 中 checked 数量始终等于遍历的数据源数量
    """
    from app.core.config import get_settings
    settings = get_settings()
    max_failures = settings.datasource_health_check_max_failures

    # 查所有数据源 (不限于 active, 让 error 的有恢复机会)
    # 如果只查 active=True, 那么 error 数据源永远不会有恢复机会
    # M6: 加 tenant_id 过滤 (跨租户隔离, admin 只能查本租户数据源)
    query = select(DataSource)
    if tenant_id:
        query = query.where(DataSource.tenant_id == tenant_id)
    result = await db_session.execute(query)
    datasources = result.scalars().all()

    summary = {"checked": 0, "healthy": 0, "unhealthy": 0, "recovered": 0, "newly_error": 0}

    # 并发 ping (IO 密集型, gather 比串行快 N 倍; 中-6 修复)
    # 带并发上限避免数据源过多时压力过大
    # 为什么是 10: 大部分场景数据源数量 < 50, 10 并发足够利用带宽,
    # 同时避免网络出口被打满。如果是 100+ 数据源, 可考虑调大。
    semaphore = asyncio.Semaphore(10)  # 最多 10 个并发 ping

    async def _ping_with_limit(ds):
        async with semaphore:
            return ds, await ping_datasource(ds)

    # gather 的 return_exceptions=True: 如果某个 ping 抛出未捕获的异常,
    # 不会导致整个 gather 失败, 而是将异常对象作为结果返回
    ping_results = await asyncio.gather(
        *(_ping_with_limit(ds) for ds in datasources),
        return_exceptions=True,
    )

    # 串行更新状态 (避免 is_active/计数竞态)
    # 虽然 SQLAlchemy session 不是线程安全的, 但这里全部在同一个协程中串行执行,
    # 不会出现竞态。但 update 操作涉及 session 的 commit, 在循环中需注意:
    # 如果某个数据源更新失败, 不会影响其他数据源的更新。
    for result in ping_results:
        # 处理 gather 返回的异常
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
            # 条件: count >= max_failures 且当前 is_active=True
            # 如果已经是 is_active=False, 不再重复审计
            if count >= max_failures and ds.is_active:
                ds.is_active = False
                summary["newly_error"] += 1
                logger.warning("数据源 %s (%s) 连续失败 %d 次, 标记为 error", ds.id, ds.name, count)
                await _audit_health(db_session, ds, "unhealthy", None, ping.error)

    # 注意: 这里没有调用 db_session.commit()
    # 调用方 (定时任务调度器) 负责统一 commit
    # 如果在此处 commit, 会与调用方的 commit 冲突
    return summary


async def _audit_health(db_session, ds: DataSource, status: str, latency_ms: int | None, error: str | None = None) -> None:
    """健康检查结果记审计 (不抛异常)。

    status 参数:
    - "recovered": 数据源从 error 恢复为 active, latency_ms 记录延迟
    - "unhealthy": 数据源因连续失败被标记为 error, error 记录失败原因

    审计日志写入失败不会阻塞健康检查流程, 仅记录 WARNING。
    因为审计是辅助功能, 健康检查本身的正确性不依赖审计。
    """
    try:
        from app.core.auth import write_audit_log
        await write_audit_log(
            db_session, tenant_id=ds.tenant_id, user_id=None,
            resource_type="data_source", action="health_check",
            status="success" if status == "recovered" else "fail",
            resource_id=ds.id,
            # detail 中只包含 latency_ms/error, 不包含密码等敏感信息
            detail={"latency_ms": latency_ms, "error": error} if error else {"latency_ms": latency_ms},
            error_message=error,
        )
    except Exception as e:
        # 审计失败不抛异常, 防止干扰主流程
        logger.warning("健康检查审计失败 (不阻塞): %s", e)
