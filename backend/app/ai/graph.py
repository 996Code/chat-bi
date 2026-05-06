from typing import Any, TypedDict

from langgraph.graph import StateGraph, END


class QueryState(TypedDict, total=False):
    question: str
    datasource_id: str
    tenant_id: str
    conversation_history: list[dict]
    schema_context: str
    raw_metadata: str
    intent: str
    sql: str
    error: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    execution_time_ms: int
    success: bool
    chart_type: str
    table_fixes: list[str]
    column_fixes: list[str]


def route_by_intent(state: QueryState) -> str:
    """根据意图路由到不同节点。"""
    if state.get("intent") == "DataQuery":
        return "schema_selection"
    return "misleading"


def handle_misleading(state: QueryState) -> QueryState:
    """处理非数据查询类问题。"""
    return {
        **state,
        "success": False,
        "error": "抱歉，我无法理解您的问题。请尝试提出与数据查询相关的问题，例如：'上个月的销售总额是多少？'",
    }


def build_graph():
    """构建 LangGraph StateGraph。

    流程：
        classify_intent → (DataQuery) → schema_selection → generate_sql → execute_sql → END
        classify_intent → (Other) → misleading → END
    """
    from app.ai.nodes.intent import classify_intent
    from app.ai.nodes.generation import generate_sql
    from app.ai.nodes.execution import execute_sql
    from app.ai.nodes.schema_selection import schema_selection_node

    graph = StateGraph(QueryState)

    # Intent node
    async def intent_node(state: QueryState) -> dict:
        intent = await classify_intent(state["question"])
        return {"intent": intent}

    # Schema selection node (two-step LLM: table selection + column selection)
    async def schema_node(state: QueryState) -> dict:
        return await schema_selection_node(state)

    # Context resolution node
    async def resolve_context_node(state: QueryState) -> dict:
        from app.ai.nodes.context_resolver import resolve_context
        history = state.get("conversation_history", [])
        resolved = resolve_context(state["question"], history)
        if resolved != state["question"]:
            return {"question": resolved}
        return {}

    # SQL generation node
    async def generation_node(state: QueryState) -> dict:
        result = await generate_sql(
            state["question"],
            state.get("schema_context", ""),
            raw_metadata=state.get("raw_metadata", ""),
        )
        sql = result.get("sql", "") if isinstance(result, dict) else (result or "")
        if not sql:
            return {
                "sql": "",
                "success": False,
                "error": "无法根据您的问题生成 SQL，请提供更具体的查询条件",
            }
        return {
            "sql": sql,
            "table_fixes": result.get("table_fixes", []),
            "column_fixes": result.get("column_fixes", []),
        }

    # Execution node with self-healing
    async def execution_node(state: QueryState) -> dict:
        from app.ai.chart_type import infer_chart_type
        from app.ai.nodes.self_heal import self_heal_sql

        result = await execute_sql(state["sql"], state["datasource_id"], tenant_id=state.get("tenant_id"))
        final_sql = state["sql"]

        # Self-healing: retry with LLM fix on failure
        if not result["success"] and state.get("schema_context"):
            heal_result = await self_heal_sql(
                question=state["question"],
                sql=state["sql"],
                error=result.get("error", ""),
                datasource_id=state.get("datasource_id", ""),
                schema_context=state["schema_context"],
                dialect="mysql",
            )
            if heal_result.get("success"):
                final_sql = heal_result.get("sql", final_sql)
                result = await execute_sql(final_sql, state["datasource_id"], tenant_id=state.get("tenant_id"))

        columns = result.get("columns", [])
        rows = result.get("rows", [])
        chart_type = "none"
        if rows and columns:
            chart_type = infer_chart_type(columns, rows)
        return {
            "sql": final_sql,
            "success": result["success"],
            "error": result.get("error"),
            "columns": columns,
            "rows": rows,
            "row_count": result.get("row_count", 0),
            "execution_time_ms": result.get("execution_time_ms"),
            "chart_type": chart_type,
        }

    # Add nodes
    graph.add_node("classify_intent", intent_node)
    graph.add_node("resolve_context", resolve_context_node)
    graph.add_node("schema_selection", schema_node)
    graph.add_node("generate_sql", generation_node)
    graph.add_node("execute_sql", execution_node)
    graph.add_node("misleading", handle_misleading)

    # Edges
    graph.set_entry_point("classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {"schema_selection": "resolve_context", "misleading": "misleading"},
    )
    graph.add_edge("resolve_context", "schema_selection")
    graph.add_edge("schema_selection", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_edge("misleading", END)
    graph.add_edge("execute_sql", END)

    return graph.compile()
