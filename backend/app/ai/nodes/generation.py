"""SQL 生成节点：LLM + 降级重试 + 表名校验 + 自动修复。"""
import asyncio
import json
import re
from difflib import SequenceMatcher

from langchain_openai import ChatOpenAI

from app.ai.prompts.query_prompt import SYSTEM_PROMPT, build_user_prompt, build_semantic_prompt
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Extract first SELECT statement from LLM response (handles markdown + explanations)
_SQL_EXTRACT = re.compile(r'(SELECT\b[\s\S]*?)(?:;|$)', re.IGNORECASE)

# Extract table names from schema_context (format: "表名: t_xxx")
_SCHEMA_TABLE_RE = re.compile(r'^表名:\s*(\S+)', re.MULTILINE)

# Extract table names from "可用表名: t_a, t_b, t_c" footer
_ALL_TABLES_RE = re.compile(r'可用表名[:\s]*([^\n]+)')

# Extract table names from SQL (FROM/JOIN clauses)
_SQL_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    re.IGNORECASE,
)

# Chinese inline comments: -- 中文注释
_SQL_COMMENT_RE = re.compile(r'--\s*[一-鿿].*$', re.MULTILINE)

# Similarity threshold for table name replacement
_TABLE_NAME_SIM_THRESHOLD = 0.5


def _clean_sql(raw: str) -> str:
    # Strip markdown code blocks
    sql = raw.strip()
    if sql.startswith("```"):
        sql = re.sub(r'^```(?:sql)?\s*', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\s*```$', '', sql)
    # Extract first SELECT statement
    m = _SQL_EXTRACT.search(sql)
    if m:
        return m.group(1).strip()
    # Fallback: return as-is
    return sql.strip()


def _strip_chinese_comments(sql: str) -> str:
    """移除 SQL 中的中文行内注释（-- 中文...）。"""
    return _SQL_COMMENT_RE.sub('', sql).strip()


def _extract_all_valid_tables(schema_context: str) -> set[str]:
    """从 schema_context 中提取所有合法表名（详情表 + 可用表名列表）。"""
    tables = set(_SCHEMA_TABLE_RE.findall(schema_context))
    # Also parse the "可用表名: t_a, t_b" footer
    m = _ALL_TABLES_RE.search(schema_context)
    if m:
        for t in m.group(1).split(','):
            name = t.strip()
            if name:
                tables.add(name)
    return tables


def _extract_sql_tables(sql: str) -> set[str]:
    """从 SQL 中提取 FROM/JOIN 后的表名（排除子查询关键字和别名）。"""
    raw = _SQL_TABLE_RE.findall(sql)
    skip = {'select', 'where', 'set', 'values', 'into', 'group', 'order', 'having', 'limit', 'on'}
    return {t.lower() for t in raw if t.lower() not in skip}


def _fix_table_names(sql: str, invalid: set[str], valid: set[str]) -> str:
    """将非法表名替换为最相似的合法表名。"""
    for bad in invalid:
        best = max(valid, key=lambda t: SequenceMatcher(None, bad, t).ratio())
        if SequenceMatcher(None, bad, best).ratio() > _TABLE_NAME_SIM_THRESHOLD:
            logger.info("Replacing invalid table '%s' -> '%s'", bad, best)
            sql = re.sub(r'\b' + re.escape(bad) + r'\b', best, sql, flags=re.IGNORECASE)
    return sql


# Extract column names from schema_context (format: "  - col_name (type)")
_SCHEMA_COL_RE = re.compile(r'^\s+-\s+(\w+)\s+\(', re.MULTILINE)

# Extract column names used in SQL (after WHERE, ON, GROUP BY, ORDER BY, etc.)
_SQL_COL_RE = re.compile(
    r'\b(\w+)\s*(?:=|!=|<>|>=|<=|>|<|\s+IS\s|\s+IN\s|\s+LIKE\s|\s+BETWEEN\s)',
    re.IGNORECASE,
)


def _extract_schema_columns(schema_context: str) -> set[str]:
    """从 schema_context 中提取所有合法列名。"""
    return set(_SCHEMA_COL_RE.findall(schema_context))


def _validate_and_fix_columns(sql: str, schema_context: str, raw_metadata: str = "") -> tuple[str, list[str]]:
    """校验并修复 SQL 中的列名，特别是 created_at 幻觉问题。返回 (fixed_sql, column_fixes)。"""
    # Parse tables from SQL
    sql_tables = _extract_sql_tables(sql)
    if not sql_tables:
        return sql, []

    # Build a map of table -> columns from raw_metadata
    table_cols: dict[str, set[str]] = {}
    if raw_metadata:
        try:
            metadata = json.loads(raw_metadata)
            for model in metadata.get("models", []):
                tname = model.get("name", "").lower()
                cols = {c.get("name", "") for c in model.get("columns", []) if c.get("name")}
                table_cols[tname] = cols
        except (json.JSONDecodeError, TypeError):
            pass

    column_fixes = []
    # For each table in SQL, check if hallucinated columns exist in that specific table
    hallucinated_cols = {"created_at", "updated_at", "created_time", "update_time"}
    for hc in hallucinated_cols:
        if not re.search(r'\b' + hc + r'\b', sql, re.IGNORECASE):
            continue

        # Check if any SQL-referenced table has this column
        has_valid_col = False
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            if hc in cols:
                has_valid_col = True
                break

        if has_valid_col:
            continue  # Column exists in at least one referenced table

        # Find replacement: look for time-like columns in the referenced tables
        for t in sql_tables:
            cols = table_cols.get(t.lower(), set())
            time_cols = [c for c in cols if any(
                kw in c.lower() for kw in ("time", "date", "at", "timestamp", "ts")
            )]
            if time_cols:
                best = max(time_cols, key=lambda c: SequenceMatcher(None, hc, c).ratio())
                ratio = SequenceMatcher(None, hc, best).ratio()
                replacement = best if ratio > 0.2 else time_cols[0]
                logger.info("Fixing hallucinated column '%s' -> '%s' (table: %s)", hc, replacement, t)
                column_fixes.append(f"{hc} -> {replacement}")
                sql = re.sub(r'\b' + hc + r'\b', replacement, sql, flags=re.IGNORECASE)
                break

    return sql, column_fixes


def _validate_and_fix_tables(sql: str, schema_context: str, raw_metadata: str = "") -> tuple[str, list[str], list[str]]:
    """校验 SQL 中的表名是否在 schema 中，不匹配则自动修复。返回 (fixed_sql, table_fixes, column_fixes)。"""
    # First strip Chinese comments
    sql = _strip_chinese_comments(sql)

    table_fixes = []
    valid_tables = _extract_all_valid_tables(schema_context)
    if valid_tables:
        used_tables = _extract_sql_tables(sql)
        invalid = used_tables - valid_tables
        if invalid:
            logger.warning("LLM used invalid tables: %s, valid: %s", invalid, valid_tables)
            for bad in invalid:
                best = max(valid_tables, key=lambda t: SequenceMatcher(None, bad, t).ratio())
                if SequenceMatcher(None, bad, best).ratio() > _TABLE_NAME_SIM_THRESHOLD:
                    table_fixes.append(f"{bad} -> {best}")
            sql = _fix_table_names(sql, invalid, valid_tables)

    # Validate and fix column names (e.g. created_at hallucination)
    column_fixes = []
    sql, column_fixes = _validate_and_fix_columns(sql, schema_context, raw_metadata)
    return sql, table_fixes, column_fixes


def _build_full_schema_context(raw_metadata: str) -> str:
    """从完整 metadata 构建 schema context（用于空 SQL 重试）。"""
    from app.ai.nodes.shared_utils import append_all_table_names

    try:
        metadata = json.loads(raw_metadata)
        models = metadata.get("models", [])
    except (json.JSONDecodeError, TypeError):
        return raw_metadata

    lines = ["可用的数据库表结构（完整 schema）：", ""]
    for model in models:
        lines.append(f"表名: {model.get('name', '?')}")
        if model.get("description"):
            lines.append(f"说明: {model['description']}")
        lines.append("字段:")
        for col in model.get("columns", [])[:20]:
            nullable = "NULL" if col.get("nullable") else "NOT NULL"
            primary = " [主键]" if col.get("primary") else ""
            comment = f" — {col['comment']}" if col.get("comment") else ""
            lines.append(f"  - {col.get('name', '?')} ({col.get('type', 'unknown')}) {nullable}{primary}{comment}")
        lines.append("")

    result = "\n".join(lines)
    return append_all_table_names(result, raw_metadata)


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=settings.llm_generation_temperature,
        max_tokens=settings.llm_generation_max_tokens,
        extra_body={"enable_thinking": False},
    )


def get_fallback_llm() -> ChatOpenAI:
    """Higher temperature + max_tokens for retry when default LLM fails."""
    return ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0.7,
        max_tokens=4000,
        extra_body={"enable_thinking": False},
    )


async def _llm_generate(messages: list, llm: ChatOpenAI | None = None, attempt: str = "") -> str | None:
    """调用 LLM 并清理返回结果。返回 SQL 字符串或 None。"""
    if llm is None:
        llm = get_llm()
    # Log the full prompt for debugging
    for role, content in messages:
        logger.info("=== %s LLM %s message (%d chars) ===", attempt or "LLM", role, len(content))
        if len(content) > 500:
            logger.info("... preview: %s ...", content[:250])
            logger.info("... tail: %s", content[-250:])
        else:
            logger.info("%s", content)
    try:
        async with asyncio.timeout(30):
            response = await llm.ainvoke(messages)
    except asyncio.TimeoutError:
        logger.warning("LLM timeout")
        return None
    except Exception as e:
        logger.warning("LLM error: %s", e)
        return None

    raw = response.content.strip()
    if not raw:
        return None
    sql = _clean_sql(raw)
    return sql if sql else None


def _build_history_context(history: list[dict] | None) -> str:
    """Build conversation history context string for multi-turn queries."""
    if not history:
        return ""
    lines = ["\n## 对话历史（参考上下文，不要重复查询已有结果）"]
    for msg in history[-6:]:  # Last 3 turns (user+assistant)
        role = msg.get("role", "")
        content = msg.get("content", "")
        sql = msg.get("sql", "")
        if role == "user" and content:
            lines.append(f"用户: {content}")
        elif role == "assistant":
            if sql:
                lines.append(f"助手(已执行SQL): {sql}")
            elif content and content not in ("处理中...", "查询成功"):
                lines.append(f"助手: {content}")
    return "\n".join(lines)


async def generate_sql(
    question: str,
    schema_context: str,
    raw_metadata: str = "",
    history: list[dict] | None = None,
) -> dict:
    """生成 SQL：schema context 已经是 LLM 精选的，直接生成即可。返回 dict 包含 sql, attempt, table_fixes, column_fixes。"""
    history_ctx = _build_history_context(history)
    attempt = 1
    # Attempt 1: direct generation with selected schema
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", build_user_prompt(question, schema_context) + history_ctx),
    ]
    sql = await _llm_generate(messages, attempt="attempt1")

    # Attempt 2: full schema context (fallback if selected schema was insufficient)
    if sql is None and raw_metadata:
        logger.info("LLM returned empty, retrying with full schema")
        attempt = 2
        full_schema = _build_full_schema_context(raw_metadata)
        retry_messages = [
            ("system", SYSTEM_PROMPT),
            ("human", build_user_prompt(question, full_schema)),
        ]
        sql = await _llm_generate(retry_messages, attempt="attempt2")

    # Attempt 3: higher temperature LLM with simpler prompt
    if sql is None:
        logger.info("Retrying with higher temperature LLM")
        attempt = 3
        simple_prompt = (
            f"Based on the question: {question}\n\n"
            f"And this database schema:\n{schema_context}\n\n"
            f"Generate a valid MySQL SELECT query. "
            f"Only return the SQL statement, no explanation."
        )
        fb_llm = get_fallback_llm()
        sql = await _llm_generate(
            [("system", "You are a SQL expert. Generate accurate SELECT queries."),
             ("human", simple_prompt)],
            fb_llm,
            attempt="attempt3",
        )

    if sql is None:
        return {"sql": "", "attempt": attempt, "table_fixes": [], "column_fixes": []}

    # Validate and fix table names + strip Chinese comments
    logger.info("Before validation: sql=%s", sql[:200])
    sql, table_fixes, column_fixes = _validate_and_fix_tables(sql, schema_context, raw_metadata)
    logger.info("After validation: sql=%s", sql[:200])
    return {"sql": sql, "attempt": attempt, "table_fixes": table_fixes, "column_fixes": column_fixes}
