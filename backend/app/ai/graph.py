"""
LangGraph 状态图定义 — AI 查询管线的"骨架"

=== 文件定位 ===
本文件是 LangGraph AI 查询流程的**核心骨架**，定义了整个查询管线的节点和边。
整个 AI 查询流程为：
  用户提问 → 意图识别 → 上下文补全 → Schema 选择 → SQL 生成 → 执行查询
  → SQL 自愈（失败时）→ 图表推断 → 返回结果

=== LangGraph 核心概念 ===

**StateGraph（状态图）**：
  LangGraph 的核心数据结构，把 AI 工作流建模为一张"图"。
  - 节点（Node）：每个节点是一个 async 函数，接收 state，返回 state 更新
  - 边（Edge）：定义节点之间的执行顺序，可以是普通边或条件边
  - State（状态）：节点之间共享的数据对象，用 TypedDict 定义

**为什么用 LangGraph 而不是普通函数调用？**
  1. 可观测性：每一步的状态变化都可以追踪，方便调试
  2. 可恢复性：中断后可以从任意节点恢复执行
  3. 条件路由：根据 state 动态决定下一步走哪个节点
  4. 可视化：可以自动生成流程图，帮助理解复杂流程

=== QueryState 状态对象 ===
状态对象是节点之间传递数据的"信封"。每个节点从 state 读取输入，
处理后将结果写回 state（通过返回 dict）。

字段说明：
  - question: 用户原始自然语言问题
  - datasource_id: 目标数据源 ID
  - tenant_id: 租户 ID（多租户隔离）
  - conversation_history: 对话历史（多轮上下文）
  - schema_context: 选中的表结构（DDL 文本）
  - raw_metadata: 原始元数据（JSON 字符串）
  - intent: 意图分类结果（DataQuery / Other）
  - sql: 生成的 SQL 语句
  - error: 错误信息（失败时）
  - columns: 查询结果的列名列表
  - rows: 查询结果的数据行
  - row_count: 返回行数
  - execution_time_ms: SQL 执行耗时
  - success: 是否成功
  - chart_type: 推断的图表类型
  - table_fixes: 表名修正记录
  - column_fixes: 列名修正记录

=== 与其他文件的关系 ===
- app/ai/nodes/intent.py — 意图识别节点
- app/ai/nodes/context_resolver.py — 上下文补全节点
- app/ai/nodes/schema_selection.py — Schema 选择节点
- app/ai/nodes/generation.py — SQL 生成节点
- app/ai/nodes/execution.py — SQL 执行节点
- app/ai/nodes/self_heal.py — SQL 自愈节点
- app/services/pipeline_executor.py — 调用本文件的 build_graph() 执行管线

=== 关键 Python 概念 ===
- TypedDict: Python 3.8+ 的类型，用于定义有类型提示的字典
- total=False: 表示所有字段都是可选的（节点可以只返回部分字段）
- StateGraph: LangGraph 提供的状态图类
- END: LangGraph 的特殊常量，表示流程结束
"""
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END


class QueryState(TypedDict, total=False):
    """
    查询状态 — 节点之间共享的数据对象。

    LangGraph 的 State 必须是 TypedDict，这样 LangGraph 才知道如何合并
    多个节点的返回值。total=False 表示所有字段都是可选的。

    工作原理：
      1. 初始 state 由调用方传入（question, datasource_id, tenant_id 等）
      2. 每个节点返回一个 dict，包含要更新的字段
      3. LangGraph 自动将返回值合并到 state 中
      4. 下一个节点拿到的是更新后的完整 state

    示例：
      意图识别节点返回 {"intent": "DataQuery"}
      → state["intent"] 变成 "DataQuery"
      → 下一个节点可以通过 state.get("intent") 读取
    """
    # ── 输入字段（由调用方传入）──
    question: str                    # 用户原始问题
    datasource_id: str               # 目标数据源 ID
    tenant_id: str                   # 租户 ID
    conversation_history: list[dict] # 对话历史

    # ── 中间字段（节点之间传递）──
    schema_context: str              # 选中的表结构（DDL）
    raw_metadata: str                # 原始元数据（JSON）
    intent: str                      # 意图分类结果

    # ── 输出字段（最终结果）──
    sql: str                         # 生成的 SQL
    error: str                       # 错误信息
    columns: list[str]               # 结果列名
    rows: list[dict]                 # 结果数据
    row_count: int                   # 返回行数
    execution_time_ms: int           # 执行耗时（毫秒）
    success: bool                    # 是否成功
    chart_type: str                  # 图表类型

    # ── 调试字段 ──
    table_fixes: list[str]           # 表名修正记录
    column_fixes: list[str]          # 列名修正记录


def route_by_intent(state: QueryState) -> str:
    """
    条件路由函数 — 根据意图决定下一步走哪个节点。

    这是 LangGraph 的"条件边"（Conditional Edge）的核心。
    条件边让流程可以根据 state 动态分支，而不是固定走某条路径。

    参数:
        state: 当前状态对象

    返回:
        str — 下一个节点的名称

    工作原理：
      1. 从 state 读取 intent 字段
      2. 如果是 "DataQuery"，返回 "schema_selection"
      3. 否则返回 "misleading"

    在 build_graph() 中通过 add_conditional_edges() 使用：
      graph.add_conditional_edges("classify_intent", route_by_intent, {...})
    """
    if state.get("intent") == "DataQuery":
        return "schema_selection"
    return "misleading"


def handle_misleading(state: QueryState) -> QueryState:
    """
    处理非数据查询类问题 — 礼貌拒绝节点。

    当用户问的不是数据查询（如"今天天气怎么样"），走这个节点。
    返回一个友好的错误提示，引导用户提出数据相关问题。

    参数:
        state: 当前状态

    返回:
        dict — 包含 success=False 和错误消息
    """
    return {
        **state,
        "success": False,
        "error": "抱歉，我无法理解您的问题。请尝试提出与数据查询相关的问题，例如：'上个月的销售总额是多少？'",
    }


def build_graph():
    """
    构建 LangGraph StateGraph — 组装查询管线的"骨架"。

    这是本文件的核心函数，把所有节点和边组装成一个可执行的图。

    流程图：
        ┌─────────────────┐
        │ 用户提问         │
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │ 意图识别         │  ← nodes/intent.py
        └────────┬────────┘
             是数据查询？
            ╱          ╲
          是            否
          ▼              ▼
      ┌──────────┐  ┌──────────┐
      │ 上下文补全│  │ 礼貌拒绝 │
      └─────┬────┘  └──────────┘
            ▼
      ┌──────────┐
      │ Schema选择│  ← nodes/schema_selection.py
      └─────┬────┘
            ▼
      ┌──────────┐
      │ SQL生成   │  ← nodes/generation.py
      └─────┬────┘
            ▼
      ┌──────────┐
      │ SQL执行   │  ← nodes/execution.py
      └─────┬────┘
            ▼
      ┌──────────┐
      │ 图表推断   │  ← chart_type.py
      └──────────┘

    返回:
        CompiledGraph — 可执行的 LangGraph 对象，调用 .ainvoke(state) 执行

    Python 知识点:
        - 延迟导入（函数内 import）：避免循环依赖
        - async def 定义异步节点函数
        - graph.add_node(name, func) 添加节点
        - graph.add_edge(from, to) 添加普通边
        - graph.add_conditional_edges(from, router, mapping) 添加条件边
        - graph.set_entry_point(node) 设置入口节点
        - graph.compile() 编译为可执行图
    """
    # 延迟导入：避免 graph.py 和各节点文件之间的循环依赖
    # Python 允许在函数内部 import，只在该函数作用域内有效
    from app.ai.nodes.intent import classify_intent
    from app.ai.nodes.generation import generate_sql
    from app.ai.nodes.execution import execute_sql
    from app.ai.nodes.schema_selection import schema_selection_node

    # 创建状态图，绑定 QueryState 作为状态类型
    graph = StateGraph(QueryState)

    # ═══════════════════════════════════════════════════════════════
    # 节点定义 — 每个节点是一个 async 函数，接收 state，返回 state 更新
    # ═══════════════════════════════════════════════════════════════

    # ── 意图识别节点 ──
    # 调用 intent.py 的 classify_intent()，判断问题是数据查询还是其他
    async def intent_node(state: QueryState) -> dict:
        intent = await classify_intent(state["question"])
        return {"intent": intent}

    # ── Schema 选择节点 ──
    # 调用 schema_selection.py，两步 LLM：先选表，再选列
    async def schema_node(state: QueryState) -> dict:
        return await schema_selection_node(state)

    # ── 上下文补全节点 ──
    # 处理多轮对话中的代词（"它"、"那个"）和相对时间（"上个月"）
    async def resolve_context_node(state: QueryState) -> dict:
        from app.ai.nodes.context_resolver import resolve_context
        history = state.get("conversation_history", [])
        resolved = resolve_context(state["question"], history)
        # 只有解析结果不同时才更新 question
        if resolved != state["question"]:
            return {"question": resolved}
        return {}

    # ── SQL 生成节点 ──
    # 调用 generation.py，三次尝试降级策略
    async def generation_node(state: QueryState) -> dict:
        result = await generate_sql(
            state["question"],
            state.get("schema_context", ""),
            raw_metadata=state.get("raw_metadata", ""),
        )
        # 兼容两种返回格式：dict 或 str
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

    # ── SQL 执行节点（含自愈）──
    # 执行 SQL，失败时调用 self_heal.py 尝试修复
    async def execution_node(state: QueryState) -> dict:
        from app.ai.chart_type import infer_chart_type
        from app.ai.nodes.self_heal import self_heal_sql

        # 第一次执行
        result = await execute_sql(state["sql"], state["datasource_id"], tenant_id=state.get("tenant_id"))
        final_sql = state["sql"]

        # 自愈：如果失败且有 schema_context，尝试让 LLM 修复 SQL
        if not result["success"] and state.get("schema_context"):
            heal_result = await self_heal_sql(
                question=state["question"],
                sql=state["sql"],
                error=result.get("error", ""),
                datasource_id=state.get("datasource_id", ""),
                schema_context=state["schema_context"],
                dialect="mysql",
            )
            # 自愈成功，重新执行修复后的 SQL
            if heal_result.get("success"):
                final_sql = heal_result.get("sql", final_sql)
                result = await execute_sql(final_sql, state["datasource_id"], tenant_id=state.get("tenant_id"))

        # 推断图表类型
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

    # ═══════════════════════════════════════════════════════════════
    # 组装图 — 添加节点和边
    # ═══════════════════════════════════════════════════════════════

    # 添加节点：第一个参数是节点名称（字符串），第二个是节点函数
    graph.add_node("classify_intent", intent_node)
    graph.add_node("resolve_context", resolve_context_node)
    graph.add_node("schema_selection", schema_node)
    graph.add_node("generate_sql", generation_node)
    graph.add_node("execute_sql", execution_node)
    graph.add_node("misleading", handle_misleading)

    # 设置入口点：流程从这里开始
    graph.set_entry_point("classify_intent")

    # 条件边：根据 route_by_intent 的返回值决定下一步
    # mapping: {"schema_selection": "resolve_context", "misleading": "misleading"}
    # 表示如果 router 返回 "schema_selection"，则跳到 "resolve_context" 节点
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {"schema_selection": "resolve_context", "misleading": "misleading"},
    )

    # 普通边：固定从一个节点跳到下一个节点
    graph.add_edge("resolve_context", "schema_selection")
    graph.add_edge("schema_selection", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_edge("misleading", END)   # END 表示流程结束
    graph.add_edge("execute_sql", END)

    # 编译图：返回可执行的对象
    return graph.compile()
