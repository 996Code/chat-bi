"""SQL 生成节点：LLM + 表名校验 + 自动修复。"""
import asyncio
import re
from difflib import SequenceMatcher

from langchain_openai import ChatOpenAI

from app.ai.prompts.query_prompt import SYSTEM_PROMPT, build_user_prompt
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


def _validate_and_fix_tables(sql: str, schema_context: str) -> str:
    """校验 SQL 中的表名是否在 schema 中，不匹配则自动修复。"""
    # First strip Chinese comments
    sql = _strip_chinese_comments(sql)

    valid_tables = _extract_all_valid_tables(schema_context)
    if not valid_tables:
        return sql
    used_tables = _extract_sql_tables(sql)
    invalid = used_tables - valid_tables
    if invalid:
        logger.warning("LLM used invalid tables: %s, valid: %s", invalid, valid_tables)
        sql = _fix_table_names(sql, invalid, valid_tables)
    return sql


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=settings.llm_generation_temperature,
        max_tokens=settings.llm_generation_max_tokens,
    )


async def generate_sql(
    question: str,
    schema_context: str,
) -> str:
    llm = get_llm()
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", build_user_prompt(question, schema_context)),
    ]
    try:
        async with asyncio.timeout(30):
            response = await llm.ainvoke(messages)
    except asyncio.TimeoutError:
        logger.warning("generate_sql LLM timeout")
        return ""
    except Exception as e:
        logger.warning("generate_sql LLM error: %s", e)
        return ""

    raw = response.content.strip()
    sql = _clean_sql(raw)
    # Validate and fix table names + strip Chinese comments
    sql = _validate_and_fix_tables(sql, schema_context)
    logger.info("LLM SQL (first 200): %s", sql[:200])
    return sql
