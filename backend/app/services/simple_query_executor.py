"""Simple query executor for lightweight model routing.

Used when llm_simple_model is configured and query complexity is low (score <= 2).
Skips the two-step LLM schema selection and uses direct SQL generation with a simpler model.
"""
import json
import time

from app.ai.nodes.shared_utils import get_llm, LLM_NO_THINKING

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import MetadataConfig
from app.db.session import async_session_factory
from sqlalchemy import select

logger = get_logger(__name__)

_SIMPLE_SYSTEM_PROMPT = """你是一个 SQL 专家。根据用户问题和数据库结构，生成一条 MySQL SELECT 查询。

要求:
1. 只返回 SELECT 语句，不要解释
2. 使用正确的表名和字段名
3. 不要包含 markdown 代码块标记
4. 不要包含中文注释"""


async def _build_minimal_schema(raw_metadata: str) -> str:
    """Build a minimal schema context (table names + descriptions + key columns only)."""
    try:
        metadata = json.loads(raw_metadata)
    except (json.JSONDecodeError, TypeError):
        return raw_metadata

    lines = ["数据库表结构：", ""]
    for model in metadata.get("models", []):
        if model.get("_deleted"):
            continue
        name = model.get("name", "?")
        desc = model.get("description", "") or model.get("comment", "") or ""
        lines.append(f"表名: {name}")
        if desc:
            lines.append(f"说明: {desc}")
        # Only include key columns to keep context small
        key_cols = []
        for c in model.get("columns", [])[:15]:
            cname = c.get("name", "?")
            ctype = c.get("type", "?")
            ccomment = c.get("comment", "") or ""
            pk = " [主键]" if c.get("primary") else ""
            line = f"  - {cname} ({ctype}){pk}"
            if ccomment:
                line += f" — {ccomment}"
            key_cols.append(line)
        if key_cols:
            lines.append("字段:")
            lines.extend(key_cols)
        lines.append("")

    return "\n".join(lines)


def _get_simple_llm():
    """Get the lightweight model for simple queries."""
    return get_llm(max_tokens=500, temperature=0.0, model=settings.llm_simple_model)


async def execute_simple_query(
    question: str,
    datasource_id: str,
    tenant_id: str,
) -> dict:
    """Execute a simple query using the lightweight model path.

    Skips schema selection LLM calls — builds minimal schema context and generates SQL directly.
    Falls back to full pipeline result format for compatibility.
    """
    start = time.monotonic()

    # Fetch metadata
    try:
        async with async_session_factory() as db:
            query = select(MetadataConfig).where(
                MetadataConfig.datasource_id == datasource_id,
                MetadataConfig.tenant_id == tenant_id,
            )
            config_result = await db.execute(query)
            config = config_result.scalar_one_or_none()
            raw_metadata = config.config if config else ""
    except Exception as e:
        logger.warning("Simple query: failed to fetch metadata: %s", e)
        return {
            "success": False,
            "error": "无法获取数据源元数据",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": 0,
        }

    if not raw_metadata:
        return {
            "success": False,
            "error": "数据源无元数据，请先刷新数据源",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": 0,
        }

    # Build minimal schema context (no LLM selection)
    schema_context = await _build_minimal_schema(raw_metadata)

    # Generate SQL with simple model
    llm = _get_simple_llm()
    messages = [
        ("system", _SIMPLE_SYSTEM_PROMPT),
        ("human", f"问题: {question}\n\n表结构:\n{schema_context}"),
    ]

    try:
        import asyncio
        async with asyncio.timeout(20):
            response = await llm.ainvoke(messages)
    except asyncio.TimeoutError:
        logger.warning("Simple query LLM timeout")
        return {
            "success": False,
            "error": "简单模型查询超时",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as e:
        logger.warning("Simple query LLM error: %s", e)
        return {
            "success": False,
            "error": f"简单模型调用失败: {e}",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": int((time.monotonic() - start) * 1000),
        }

    raw_sql = response.content.strip()
    # Clean: strip markdown, extract SELECT
    import re
    _SQL_EXTRACT = re.compile(r'(SELECT\b[\s\S]*?)(?:;|$)', re.IGNORECASE)
    if raw_sql.startswith("```"):
        raw_sql = re.sub(r'^```(?:sql)?\s*', '', raw_sql, flags=re.IGNORECASE)
        raw_sql = re.sub(r'\s*```$', '', raw_sql)
    m = _SQL_EXTRACT.search(raw_sql)
    sql = m.group(1).strip() if m else raw_sql.strip()

    if not sql:
        return {
            "success": False,
            "error": "简单模型未返回有效 SQL",
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": int((time.monotonic() - start) * 1000),
        }

    # Execute SQL
    from app.ai.nodes.execution import execute_sql
    result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)

    # Self-heal on failure
    if not result.get("success"):
        from app.ai.nodes.self_heal import self_heal_sql
        heal_result = await self_heal_sql(
            question=question,
            sql=sql,
            error=result.get("error", ""),
            datasource_id=datasource_id,
            schema_context=schema_context,
            dialect="mysql",
        )
        if heal_result.get("success"):
            sql = heal_result.get("sql", sql)
            result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {
        "success": result.get("success", False),
        "error": result.get("error"),
        "sql": sql,
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "row_count": result.get("row_count", 0),
        "execution_time_ms": result.get("execution_time_ms") or elapsed_ms,
    }
