import asyncio
import time
from typing import Any

import sqlglot
from sqlglot import ParseError

from app.core.logging import get_logger
from app.db.session import get_db
from app.db.models import DataSource
from app.services.connection_pool import pool_manager

logger = get_logger(__name__)

MAX_ROWS = 1000


def validate_sql(sql: str) -> tuple[bool, str]:
    """使用 SQLGlot AST 验证 SQL，拒绝非 SELECT 语句。"""
    try:
        parsed = sqlglot.parse_one(sql, dialect="mysql")
    except ParseError as e:
        return False, f"SQL 语法错误: {e}"

    if not isinstance(parsed, sqlglot.exp.Select):
        return False, "仅支持 SELECT 查询"

    # 检查是否包含危险操作
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    upper_sql = sql.upper()
    for kw in dangerous_keywords:
        if kw in upper_sql:
            return False, f"禁止使用 {kw} 语句"

    return True, ""


async def execute_sql(sql: str, datasource_id: str) -> dict[str, Any]:
    """执行 SQL 并返回结果。带 30 秒超时保护。"""
    valid, error = validate_sql(sql)
    if not valid:
        return {"success": False, "error": error}

    try:
        # Get engine from pool
        pool = await pool_manager.get_pool_by_id(datasource_id)
        if not pool:
            return {
                "success": False,
                "error": "数据源连接池未初始化",
            }

        start = time.monotonic()

        async with asyncio.timeout(30):
            async with pool.connect() as conn:
                # Set read-only mode
                from sqlalchemy import text
                await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
                result = await conn.execute(text(sql))
                columns = list(result.keys())
                rows = [dict(row._mapping) for row in result.fetchall()]

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Truncate to MAX_ROWS
        truncated = False
        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
            truncated = True

        # Convert non-serializable types
        import datetime
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
        logger.error("SQL execution timed out after 30s: %s", sql[:200])
        return {"success": False, "error": "查询超时（30秒限制）"}
    except Exception as e:
        logger.error("SQL execution failed: %s — %s", sql[:200], e)
        return {"success": False, "error": f"SQL 执行失败: {e}"}
