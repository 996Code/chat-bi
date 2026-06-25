"""
T031: SQL 执行 — 连接池 + 超时 + 行数限制 + 自动 LIMIT

对标:
  - AEE-007: 执行最多 1 次; 超时控制; 大结果分块
  - v1 教训 #42: 复用 datasource_engine.py 的连接池 (已修复 dispose 泄漏)
  - Claude Code §3.4: 写操作串行 (SQL 执行是写敏感, 串行不并发)

设计:
  - 复用 DataSourceEnginePool (同步 engine) → asyncio.to_thread 包装 (不阻塞事件循环)
  - 超时: config.sql_execution_timeout (默认 30s) → asyncio.wait_for
  - 行数限制: config.sql_max_rows (默认 10000), 超限截断 + truncated 标记
  - 自动 LIMIT: 无 LIMIT 的 SELECT 自动加 (防全表扫描 OOM)
  - 失败返回 error (不抛), 调用方 (T032 自愈) 决定下一步
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass
class ExecuteResult:
    """SQL 执行结果。

    error 非空表示失败 (error 含原始错误信息, 供 T032 自愈用)。
    truncated=True 表示结果被 max_rows 截断。
    duration_ms: 执行耗时 (DSO-07 慢查询判定用)。
    """
    rows: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    truncated: bool = False
    rowcount: int = 0
    error: str | None = None
    # 原始 SQL (执行的实际 SQL, 含自动加的 LIMIT)
    executed_sql: str = ""
    # DSO-07: 执行耗时 (毫秒), 供慢查询判定
    duration_ms: int = 0


def _inject_limit(sql: str, max_rows: int) -> str:
    """无顶层 LIMIT 的 SELECT 自动加 LIMIT (max_rows + 1) (防全表扫描 + 截断检测)。

    多查 1 行: 返回 > max_rows 行说明被截断 (truncated=True)。
    用 sqlglot AST 判断顶层是否有 Limit (对标 v1 教训 #46: 不用字符串检测)。
    子查询里的 LIMIT 不算 (外层仍可能全表扫描)。
    """
    fetch_rows = max_rows + 1  # 多取 1 行用于判断是否截断
    try:
        import sqlglot
        stmt = sqlglot.parse_one(sql, read="postgres")
        # 顶层 Limit 直接挂在 stmt.args['limit'] (子查询的 Limit 不在这)
        if stmt.args.get("limit") is not None:
            return sql
        # 顶层无 Limit → 加
        return stmt.limit(fetch_rows).sql(dialect="postgres")
    except Exception:
        # parse 失败 (T030 应已拦截, 这里兜底) → 字符串兜底加
        sql_clean = sql.strip().rstrip(";").strip()
        if re.search(r"\bLIMIT\b", sql_clean, re.IGNORECASE):
            return sql
        return f"{sql_clean} LIMIT {max_rows}"


def _execute_sync(
    engine,
    sql: str,
    max_rows: int,
    timeout_seconds: int = 30,
) -> ExecuteResult:
    """同步执行 SQL (在 to_thread 里跑)。

    超时双保险:
      1. SQL 层 SET LOCAL statement_timeout (DB 主动中断, 释放连接)
      2. asyncio.wait_for 兜底 (主线程不等了, 但线程靠 DB timeout 结束)
    返回 ExecuteResult, 不抛异常 (异常转成 error 字段)。
    DSO-07: 记录执行耗时 (duration_ms) 供慢查询判定。
    """
    import time
    safe_sql = _inject_limit(sql, max_rows)
    t0 = time.monotonic()
    try:
        with engine.connect() as conn:
            # DB 侧超时 (statement_timeout): DB 主动中断查询, 释放连接/线程
            # 比 asyncio.wait_for 更可靠 — Python 无法取消线程, 但 DB 能取消查询
            conn.execute(text(f"SET LOCAL statement_timeout = '{timeout_seconds * 1000}'"))
            result = conn.execute(text(safe_sql))
            columns = list(result.keys()) if hasattr(result, "keys") else []
            rows = result.fetchall()
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            return ExecuteResult(
                rows=rows,
                columns=columns,
                truncated=truncated,
                rowcount=len(rows),
                executed_sql=safe_sql,
                duration_ms=round((time.monotonic() - t0) * 1000),
            )
    except Exception as e:
        # 保留原始错误信息 (含错误码, 供 T032 自愈映射)
        return ExecuteResult(
            error=str(e), executed_sql=safe_sql,
            duration_ms=round((time.monotonic() - t0) * 1000),
        )


async def execute_sql(
    sql: str,
    datasource_id: str,
    url: str,
    engine_pool,
    timeout: int | None = None,
    max_rows: int | None = None,
) -> ExecuteResult:
    """执行 SQL (只读查询), 返回 ExecuteResult。

    Args:
        sql: 已通过 T030 三层校验的 SELECT SQL
        datasource_id: 数据源 id (连接池 key)
        url: 数据库连接 URL (已解密)
        engine_pool: DataSourceEnginePool (注入, 复用连接)
        timeout: 超时秒数 (None → config.sql_execution_timeout)
        max_rows: 最大行数 (None → config.sql_max_rows)

    Returns:
        ExecuteResult — error 非空表示失败 (不抛, 调用方决定自愈/终止)

    设计:
      - engine 从池复用 (不新建, 对标 v1 #42)
      - 同步 engine → asyncio.to_thread (不阻塞事件循环)
      - 超时 → wait_for, 超时返回 error
      - 自动加 LIMIT (无 LIMIT 的全表扫描防护)
    """
    from app.core.config import get_settings
    settings = get_settings()
    timeout = timeout if timeout is not None else settings.sql_execution_timeout
    max_rows = max_rows if max_rows is not None else settings.sql_max_rows

    # 从连接池复用 engine (不新建)
    engine = engine_pool.get_or_create(datasource_id, url)

    import time
    t_start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_execute_sync, engine, sql, max_rows, timeout),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        elapsed = round((time.monotonic() - t_start) * 1000)
        logger.warning("SQL 执行超时 (%ds, 耗时 %dms): %s", timeout, elapsed, sql[:100])
        return ExecuteResult(
            error=f"SQL 执行超时 ({timeout}秒), 可能是慢查询或数据量过大",
            executed_sql=sql,
            duration_ms=elapsed,
        )
    except Exception as e:
        elapsed = round((time.monotonic() - t_start) * 1000)
        logger.warning("SQL 执行异常 (耗时 %dms): %s", elapsed, e)
        return ExecuteResult(error=str(e), executed_sql=sql, duration_ms=elapsed)
