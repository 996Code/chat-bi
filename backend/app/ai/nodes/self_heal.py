"""SQL 自愈节点：执行失败时分析错误并重试修正。"""
import asyncio
import re
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.ai.nodes.generation import get_llm

logger = get_logger(__name__)

# MySQL error code pattern: (errno: 1054)
_ERROR_CODE_RE = re.compile(r'\(errno:\s*(\d+)\)')

# 可自动修复的常见错误模式
AUTO_FIX_RULES = {
    "1146": "表不存在",
    "1054": "列不存在",
    "1064": "SQL 语法错误",
    "1049": "数据库不存在",
}


def extract_error_code(error: str) -> str | None:
    """从 MySQL 错误信息中提取错误代码。"""
    match = re.search(r"\((\d+)\)", error)
    if match:
        return match.group(1)
    return None


def build_fix_prompt(question: str, failed_sql: str, error: str, retry_count: int, schema_context: str) -> str:
    """构建 SQL 修正 prompt。"""
    error_code = extract_error_code(error) or ""
    error_desc = AUTO_FIX_RULES.get(error_code, error[:200])

    return f"""你是 SQL 修复专家。请修复以下 SQL 的错误。

原始问题：{question}
失败的 SQL：{failed_sql}
错误信息：{error}
错误类型：{error_desc}

{f"这是第 {retry_count} 次重试，请仔细检查。" if retry_count > 1 else ""}

{f"可用的表结构：\n{schema_context}" if schema_context else ""}

请只返回修复后的 SQL，不要包含任何解释或 markdown 代码块。"""


def _strip_markdown(raw: str) -> str:
    """去除 markdown 代码块。"""
    sql = re.sub(r'^```(?:\w+)?\s*', '', raw, flags=re.MULTILINE).strip()
    sql = re.sub(r'\s*```\s*$', '', sql).strip()
    return sql


def extract_error_code(error_msg: str) -> str:
    """从 MySQL 错误消息中提取错误码。"""
    m = _ERROR_CODE_RE.search(error_msg)
    return m.group(1) if m else ""


async def self_heal_sql(
    question: str,
    sql: str,
    error: str,
    datasource_id: str = "",
    schema_context: str = "",
    dialect: str = "mysql",
    retry_count: int = 1,
) -> dict[str, Any]:
    """迭代式修复失败的 SQL，执行修复后的 SQL 并返回结果。"""
    from app.ai.nodes.execution import execute_sql

    while retry_count <= settings.llm_self_heal_max_retries:
        prompt = build_fix_prompt(question, sql, error, retry_count, schema_context)

        try:
            llm = get_llm()
            async with asyncio.timeout(30):
                response = await llm.ainvoke([
                    ("system", "你只生成 SQL，不解释。"),
                    ("human", prompt),
                ])
            fixed_sql = _strip_markdown(response.content)

            if not fixed_sql:
                logger.warning("Self-heal LLM returned empty, retry %d", retry_count)
                retry_count += 1
                continue

            result = await execute_sql(fixed_sql, datasource_id, dialect)

            if result["success"]:
                logger.info("SQL self-heal succeeded on retry %d: %s", retry_count, fixed_sql[:200])
                return {"success": True, "sql": fixed_sql, "fixed": True, **result}

            logger.info("SQL fix attempt %d still failed: %s", retry_count, result.get("error"))
            error = result.get("error", "")
            retry_count += 1

        except Exception as e:
            logger.error("Self-healing LLM call failed: %s", e)
            retry_count += 1

    logger.warning("SQL self-healing exceeded max retries for: %s", sql[:200])
    return {"success": False, "error": f"SQL 修复失败（已重试 {settings.llm_self_heal_max_retries} 次）"}
