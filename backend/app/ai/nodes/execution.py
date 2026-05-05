import asyncio
import datetime
import re
import time
from typing import Any

import sqlglot
from sqlalchemy import select, text
from sqlglot import ParseError

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.db.models import DataSource
from app.services.connection_pool import pool_manager

logger = get_logger(__name__)


def validate_sql(sql: str, dialect: str = "mysql") -> tuple[bool, str]:
    """使用 SQLGlot AST 验证 SQL，拒绝非 SELECT 语句。"""
    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        return False, f"SQL 语法错误: {e}"

    if not isinstance(parsed, sqlglot.exp.Select):
        return False, "仅支持 SELECT 查询"

    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    pattern = re.compile(r'\b(' + '|'.join(dangerous_keywords) + r')\b', re.IGNORECASE)
    match = pattern.search(sql)
    if match:
        return False, f"禁止使用 {match.group(1)} 语句"

    return True, ""


async def execute_sql(sql: str, datasource_id: str, dialect: str = "mysql", tenant_id: str | None = None) -> dict[str, Any]:
    """执行 SQL 并返回结果。带 30 秒超时保护。"""
    valid, error = validate_sql(sql, dialect)
    if not valid:
        return {"success": False, "error": error}

    try:
        engine = await pool_manager.get_pool_by_id(datasource_id)
        if not engine:
            async for db in get_db():
                query = select(DataSource).where(DataSource.id == datasource_id)
                if tenant_id:
                    query = query.where(DataSource.tenant_id == tenant_id)
                result = await db.execute(query)
                ds = result.scalar_one_or_none()
                if not ds:
                    return {"success": False, "error": "数据源不存在"}
                if not ds.is_active:
                    return {"success": False, "error": "数据源已禁用"}
                engine = await pool_manager.get_pool(ds)
                break
            if not engine:
                return {"success": False, "error": "数据源连接池未初始化"}

        start = time.monotonic()

        async with asyncio.timeout(settings.sql_execution_timeout):
            async with engine.connect() as conn:
                try:
                    await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                except Exception:
                    await conn.execute(text("SET default_transaction_read_only = on"))
                result = await conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(row._mapping) for row in result.fetchall()]

        elapsed_ms = int((time.monotonic() - start) * 1000)

        truncated = False
        if len(rows) > settings.query_max_rows:
            rows = rows[:settings.query_max_rows]
            truncated = True

        for row in rows:
            for k, v in row.items():
                if isinstance(v, (datetime.datetime, datetime.date)):
                    row[k] = str(v)
                elif isinstance(v, bytes):
                    row[k] = v.decode("utf-8", errors="replace")

        return {
            "success": True,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "execution_time_ms": elapsed_ms,
        }

    except asyncio.TimeoutError:
        logger.error("SQL execution timed out after %ds: %s", settings.sql_execution_timeout, sql[:200])
        return {"success": False, "error": f"查询超时（{settings.sql_execution_timeout}秒限制）"}
    except Exception as e:
        logger.error("SQL execution failed: %s — %s", sql[:200], e)
        return {"success": False, "error": f"SQL 执行失败: {e}"}