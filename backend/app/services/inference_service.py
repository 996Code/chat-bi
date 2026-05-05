"""LLM 驱动的语义推断：关联关系、指标。

使用 LLM 推断表之间的关联关系和常用指标，LLM 不可用时直接报错。
"""
import json
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def infer_relationships(models: list[dict]) -> list[dict]:
    """使用 LLM 推断表之间的关联关系。"""
    if not models:
        return []

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise RuntimeError("langchain_openai 不可用，无法推断关联关系")

    # Build table structure description
    tables_desc = []
    for model in models[:20]:
        cols = []
        for col in model.get("columns", [])[:20]:
            col_info = col.get("name", "")
            if col.get("comment"):
                col_info += f"({col['comment']})"
            if col.get("primary"):
                col_info += " [PK]"
            col_key = col.get("column_key", "")
            if col_key in ("MUL", "FK"):
                col_info += " [FK]"
            cols.append(col_info)
        desc = model.get("description", "") or model.get("comment", "") or ""
        table_line = f"- {model.get('name', '?')}" + (f" ({desc})" if desc else "")
        table_line += f": {', '.join(cols)}"
        tables_desc.append(table_line)

    prompt = f"""根据以下数据库表结构，推断表之间的外键关联关系。

表结构:
{chr(10).join(tables_desc)}

要求:
1. 根据字段名模式（如 xxx_id）和语义推断关联关系
2. 只输出确定存在的关联，不要猜测
3. 每行一个关联，格式: from_table.from_column -> to_table.to_column
4. 只输出关联列表，不要其他内容

示例:
orders.user_id -> users.id
order_items.order_id -> orders.id"""

    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=0.0,
        max_tokens=1000,
    )
    response = await llm.ainvoke(prompt)
    text = response.content.strip()

    # Parse LLM output into relationship dicts
    relationships = []
    table_names = {m.get("name", "").lower() for m in models}
    for line in text.split("\n"):
        line = line.strip().lstrip("-0123456789.) ")
        if "->" not in line:
            continue
        parts = line.split("->")
        if len(parts) != 2:
            continue
        left = parts[0].strip()
        right = parts[1].strip()

        if "." not in left or "." not in right:
            continue
        from_table, from_col = left.rsplit(".", 1)
        to_table, to_col = right.rsplit(".", 1)

        # Validate table names exist
        if from_table.lower() not in table_names or to_table.lower() not in table_names:
            continue

        actual_from = next((m["name"] for m in models if m["name"].lower() == from_table.lower()), None)
        actual_to = next((m["name"] for m in models if m["name"].lower() == to_table.lower()), None)
        if not actual_from or not actual_to:
            continue

        relationships.append({
            "from_table": actual_from,
            "from_column": from_col,
            "to_table": actual_to,
            "to_column": to_col,
        })

    return relationships


async def infer_metrics(models: list[dict], relationships: list[dict] | None = None) -> list[dict]:
    """使用 LLM 推断常用业务指标。"""
    if not models:
        return []

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise RuntimeError("langchain_openai 不可用，无法推断指标")

    tables_desc = []
    for model in models[:15]:
        cols = []
        for col in model.get("columns", [])[:15]:
            col_info = col.get("name", "")
            if col.get("comment"):
                col_info += f"({col['comment']})"
            cols.append(col_info)
        desc = model.get("description", "") or model.get("comment", "") or model.get("name", "")
        table_line = f"- {model.get('name', '?')} ({desc}): {', '.join(cols)}"
        tables_desc.append(table_line)

    rels_desc = ""
    if relationships:
        rels_desc = "\n关联关系:\n" + "\n".join(
            f"- {r['from_table']}.{r['from_column']} -> {r['to_table']}.{r['to_column']}"
            for r in relationships[:10]
        )

    prompt = f"""根据以下数据库表结构，推断 6-10 个最常用的业务指标。

表结构:
{chr(10).join(tables_desc)}
{rels_desc}

要求:
1. 指标名称用中文，简短明确
2. 覆盖不同类型：计数(COUNT)、求和(SUM)、平均值(AVG)
3. 优先选择业务人员最关心的指标
4. 每行一个指标，格式: 名称|表达式|说明
5. 表达式使用 SQL 聚合函数，表名和字段名必须与上面完全一致
6. 只输出指标列表，不要其他内容

示例:
订单总数|COUNT(t_orders.id)|统计订单总数
订单金额合计|SUM(t_orders.total_amount)|计算订单总金额"""

    llm = ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=0.0,
        max_tokens=800,
    )
    response = await llm.ainvoke(prompt)
    text = response.content.strip()

    metrics = []
    for line in text.split("\n"):
        line = line.strip().lstrip("-0123456789.) ")
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        expression = parts[1].strip()
        description = parts[2].strip()
        if name and expression:
            metrics.append({
                "name": name,
                "expression": expression,
                "description": description,
            })

    return metrics[:10]
