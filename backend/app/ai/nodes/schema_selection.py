"""Schema 选择节点：两步 LLM 交互，替代 RAG 检索。

Step 1: 给 LLM 所有表名+说明+关联关系，让其返回相关表
Step 2: 给 LLM 选中表的字段列表，让其返回需要的字段
"""
import json

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import MetadataConfig
from app.db.session import async_session_factory
from sqlalchemy import select

logger = get_logger(__name__)

TABLE_SELECTION_SYSTEM = """你是一个数据库专家。根据用户问题，从以下表列表中选出相关的表。

要求:
1. 只选择确实需要的表，不要多选
2. 考虑表之间的关联关系，如果需要 JOIN 就把关联表也选上
3. 每行输出一个表名，不要编号，不要解释
4. 只输出表名，不要其他内容"""

COLUMN_SELECTION_SYSTEM = """你是一个数据库专家。根据用户问题和选中的表，从字段列表中选出需要的字段。

要求:
1. 必须包含 JOIN 所需的外键字段（如 xxx_id）
2. 必须包含主键字段
3. 包含问题中明确提到的字段
4. 包含 GROUP BY / WHERE / ORDER BY 可能需要的字段
5. 每行输出: 表名.字段名，不要编号，不要解释
6. 只输出需要的字段，不要其他内容"""


async def _select_tables(question: str, metadata: dict) -> list[str]:
    """Step 1: LLM 选择相关表。"""
    models = metadata.get("models", [])
    rels = metadata.get("relationships", [])

    if not models:
        return []

    # 构建轻量表列表
    table_lines = []
    for m in models:
        if m.get("_deleted"):
            continue
        name = m.get("name", "?")
        desc = m.get("description", "") or m.get("comment", "") or ""
        line = f"- {name}"
        if desc:
            line += f" ({desc})"
        table_lines.append(line)

    # 添加关联关系
    if rels:
        table_lines.append("")
        table_lines.append("表之间的关联关系:")
        for r in rels[:20]:
            ft = r.get("from_table", "")
            fc = r.get("from_column", "")
            tt = r.get("to_table", "")
            tc = r.get("to_column", "")
            if ft and fc and tt and tc:
                table_lines.append(f"- {ft}.{fc} -> {tt}.{tc}")

    prompt = f"""表列表:
{chr(10).join(table_lines)}

用户问题: {question}

请选出相关的表。"""

    llm = ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0.0,
        max_tokens=300,
        extra_body={"enable_thinking": False},
    )

    try:
        response = await llm.ainvoke([
            ("system", TABLE_SELECTION_SYSTEM),
            ("human", prompt),
        ])
        text = response.content.strip()
        table_names = {m["name"] for m in models if not m.get("_deleted")}
        selected = []
        for line in text.split("\n"):
            name = line.strip().lstrip("-0123456789.) *")
            if name in table_names and name not in selected:
                selected.append(name)
        logger.info("Table selection: %s -> %s", question[:50], selected)
        return selected
    except Exception as e:
        logger.warning("Table selection failed: %s", e)
        return []


async def _select_columns(question: str, selected_tables: list[str], metadata: dict) -> dict[str, list[str]]:
    """Step 2: LLM 选择需要的字段。返回 {table_name: [column_names]}。"""
    models = metadata.get("models", [])
    model_map = {m["name"]: m for m in models}

    # 构建选中表的字段列表
    col_lines = []
    for t in selected_tables:
        m = model_map.get(t)
        if not m:
            continue
        desc = m.get("description", "") or m.get("comment", "") or ""
        col_lines.append(f"表: {t} ({desc})")
        for c in m.get("columns", []):
            cname = c.get("name", "?")
            ctype = c.get("type", "?")
            ccomment = c.get("comment", "") or ""
            pk = " [主键]" if c.get("primary") else ""
            fk = " [外键]" if c.get("column_key") in ("FK", "MUL") else ""
            nullable = "NULL" if c.get("nullable") else "NOT NULL"
            line = f"  - {cname} ({ctype}) {nullable}{pk}{fk}"
            if ccomment:
                line += f" — {ccomment}"
            col_lines.append(line)
        col_lines.append("")

    if not col_lines:
        return {}

    prompt = f"""选中的表和字段:
{chr(10).join(col_lines)}

用户问题: {question}

请选出需要的字段。"""

    llm = ChatOpenAI(
        model=settings.llm_model,
        openai_api_base=settings.llm_base_url,
        openai_api_key=settings.llm_api_key,
        temperature=0.0,
        max_tokens=500,
        extra_body={"enable_thinking": False},
    )

    try:
        response = await llm.ainvoke([
            ("system", COLUMN_SELECTION_SYSTEM),
            ("human", prompt),
        ])
        text = response.content.strip()

        # 解析 "表名.字段名" 格式
        result: dict[str, list[str]] = {t: [] for t in selected_tables}
        for line in text.split("\n"):
            line = line.strip().lstrip("-0123456789.) *")
            if "." not in line:
                continue
            parts = line.rsplit(".", 1)
            if len(parts) != 2:
                continue
            tname, cname = parts[0].strip(), parts[1].strip()
            if tname in result and cname:
                result[tname].append(cname)

        logger.info("Column selection: %s", result)
        return result
    except Exception as e:
        logger.warning("Column selection failed: %s", e)
        return {}


def _build_schema_context(selected_tables: list[str], selected_columns: dict[str, list[str]], metadata: dict) -> str:
    """根据 LLM 选择的表和字段，构建精确的 schema context。"""
    models = metadata.get("models", [])
    rels = metadata.get("relationships", [])
    model_map = {m["name"]: m for m in models}

    # 构建关联关系 map
    rel_map: dict[str, list[dict]] = {}
    for r in rels:
        ft = r.get("from_table", "")
        fc = r.get("from_column", "")
        tt = r.get("to_table", "")
        tc = r.get("to_column", "")
        if ft and fc and tt and tc:
            rel_map.setdefault(ft, []).append({
                "column": fc,
                "referenced_table": tt,
                "referenced_column": tc,
            })
            rel_map.setdefault(tt, []).append({
                "column": tc,
                "referenced_table": ft,
                "referenced_column": fc,
            })

    lines = ["可用的数据库表结构：", ""]
    for t in selected_tables:
        m = model_map.get(t)
        if not m:
            continue
        lines.append(f"表名: {t}")
        desc = m.get("description", "") or m.get("comment", "")
        if desc:
            lines.append(f"说明: {desc}")
        lines.append("字段:")

        cols = selected_columns.get(t, [])
        all_cols = m.get("columns", [])
        # 如果 LLM 没选任何字段，给全部字段（兜底）
        if not cols:
            cols = [c.get("name", "") for c in all_cols]

        # 确保主键和外键字段始终包含
        for c in all_cols:
            cname = c.get("name", "")
            if c.get("primary") or c.get("column_key") in ("PRI", "FK", "MUL"):
                if cname not in cols:
                    cols.append(cname)

        # 按选中顺序输出字段
        col_map = {c.get("name"): c for c in all_cols}
        for cname in cols:
            c = col_map.get(cname)
            if not c:
                continue
            nullable = "NULL" if c.get("nullable") else "NOT NULL"
            primary = " [主键]" if c.get("primary") else ""
            comment = f" — {c['comment']}" if c.get("comment") else ""
            lines.append(f"  - {c.get('name', '?')} ({c.get('type', 'unknown')}) {nullable}{primary}{comment}")

        if t in rel_map:
            lines.append("关联:")
            for rel in rel_map[t]:
                lines.append(
                    f"  - {rel['column']} -> {rel['referenced_table']}.{rel['referenced_column']}"
                )
        lines.append("")

    return "\n".join(lines)


async def schema_selection_node(state: dict) -> dict:
    """两步 LLM schema 选择，替代 RAG 检索。

    Step 1: LLM 选择相关表
    Step 2: LLM 选择需要的字段
    构建: 精确的 schema_context 供 SQL 生成使用
    """
    question = state.get("question", "")
    datasource_id = state.get("datasource_id", "")
    tenant_id = state.get("tenant_id", "")

    if not datasource_id or not tenant_id:
        logger.warning("Schema selection: missing datasource_id or tenant_id")
        return {"schema_context": "", "raw_metadata": ""}

    # 从 DB 获取 metadata
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
        logger.warning("Schema selection: failed to fetch metadata: %s", e)
        raw_metadata = ""

    if not raw_metadata:
        logger.info("Schema selection: no metadata for datasource %s", datasource_id)
        return {"schema_context": "", "raw_metadata": ""}

    metadata = json.loads(raw_metadata)

    # Step 1: 选择表
    selected_tables = await _select_tables(question, metadata)

    if not selected_tables:
        logger.warning("Schema selection: LLM returned no tables, using all as fallback")
        selected_tables = [m["name"] for m in metadata.get("models", [])[:5] if not m.get("_deleted")]

    # Step 2: 选择字段
    selected_columns = await _select_columns(question, selected_tables, metadata)

    # 构建 schema context
    schema_context = _build_schema_context(selected_tables, selected_columns, metadata)

    # 附加所有表名列表（防止 LLM 捏造表名）
    all_table_names = [m["name"] for m in metadata.get("models", []) if not m.get("_deleted")]
    schema_context += f"\n可用表名: {', '.join(all_table_names)}\n"

    logger.info(
        "Schema selection: %d tables selected for question: %s",
        len(selected_tables),
        question[:80],
    )
    logger.info("Schema context preview:\n%s", schema_context[:800])

    return {"schema_context": schema_context, "raw_metadata": raw_metadata}