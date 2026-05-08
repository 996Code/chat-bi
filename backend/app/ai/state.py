"""
LangGraph 状态定义模块 (State Definition)
==========================================

本文件定义了 ChatBI AI 管道的核心状态结构 —— QueryState。
在 LangGraph 中，状态 (State) 是所有节点 (Node) 之间传递数据的唯一载体，
类似于一条"流水线"上的传送带：每个节点从传送带上读取数据、处理后把结果放回传送带。

核心概念
--------
1. **状态 (State)**：LangGraph 图中流动的数据容器。每个节点接收当前状态，
   返回一个字典，LangGraph 会自动将返回的字典合并 (merge) 回状态中。
   这就是为什么节点只需要返回"变化的部分"，不需要返回完整状态。

2. **TypedDict**：Python 的类型提示工具（typing 模块），用来给字典加上类型注解。
   与 dataclass / Pydantic Model 的区别：
   - TypedDict：轻量级，只做类型提示，运行时仍然是普通 dict，零性能开销
   - dataclass：Python 标准库，会生成 __init__ 等方法，适合业务对象
   - Pydantic Model：带运行时校验，适合 API 输入输出
   LangGraph 选择 TypedDict 是因为：状态本质上就是一个字典，不需要实例化、
   不需要校验，只需要类型提示让 IDE 和 mypy 帮我们检查字段名拼错。

3. **total=False**：TypedDict 的参数，表示"不是所有字段都必须存在"。
   这对 LangGraph 至关重要 —— 图的入口节点只设置 question 等少量字段，
   后续节点逐步补充字段（如 intent、sql、rows 等）。
   如果 total=True（默认），Python 类型检查器会要求创建时提供所有字段，
   这在 LangGraph 中是不现实的。

状态流转过程
------------
用户提问后，状态在节点间按以下顺序流转（每个节点读取上游字段、写入下游字段）：

  classify_intent        读取: question
                        写入: intent

  resolve_context        读取: question, conversation_history
                        写入: question（可能被补全）

  schema_selection       读取: question, datasource_id, tenant_id
                        写入: schema_context, raw_metadata

  generate_sql           读取: question, schema_context, raw_metadata
                        写入: sql, table_fixes, column_fixes
                        失败时写入: sql="", success=False, error

  execute_sql            读取: sql, datasource_id, tenant_id, schema_context
                        写入: sql（可能被自愈修复）, success, error, columns, rows,
                              row_count, execution_time_ms, chart_type

  misleading             读取: （无特定字段）
                        写入: success=False, error

与其他文件的关系
----------------
- 被导入方：`app/ai/graph.py` 用 QueryState 创建 StateGraph 并定义节点
- 节点实现：`app/ai/nodes/` 目录下各节点函数接收 QueryState 的字段作为参数
- API 层：`app/api/query.py` 构建 QueryState 的初始值（question, datasource_id 等）
           并调用 graph.invoke() 启动整个流程
"""

from typing import TypedDict


class QueryState(TypedDict, total=False):
    """ChatBI 查询状态 —— LangGraph 图中所有节点共享的数据结构。

    每个字段都代表 AI 管道某个阶段的数据。字段按处理流程大致分为四组：
    1. 输入组：question, datasource_id, tenant_id, conversation_history
    2. 理解组：intent, schema_context, raw_metadata
    3. 生成组：sql, table_fixes, column_fixes
    4. 结果组：success, error, columns, rows, row_count, execution_time_ms, chart_type

    因为 total=False，所有字段都是可选的（Optional）。
    节点通过 state.get("field_name", default) 安全读取可能不存在的字段。
    """

    # ── 输入组：由 API 层设置，整个流程的起点 ──────────────────────────

    question: str  # 用户的自然语言问题，如"上个月销售额是多少？"
    # 设置时机：API 层调用 graph.invoke() 时传入
    # 读取节点：classify_intent, resolve_context, schema_selection, generate_sql, self_heal
    # 特殊行为：resolve_context 节点可能修改此字段（把追问补全为完整问题）

    datasource_id: str  # 数据源 ID，指向要查询的具体数据库
    # 设置时机：API 层调用 graph.invoke() 时传入
    # 读取节点：schema_selection, execute_sql

    tenant_id: str  # 租户 ID，用于多租户数据隔离
    # 设置时机：API 层调用 graph.invoke() 时传入
    # 读取节点：schema_selection, execute_sql

    conversation_history: list[dict]  # 对话历史，格式为 [{"role": "user", "content": "..."}, ...]
    # 设置时机：API 层调用 graph.invoke() 时传入（可选，首轮对话为空列表）
    # 读取节点：resolve_context（用于追问补全）
    # 用途：让 AI 理解"那上个月呢？"这类指代上轮对话的追问

    # ── 理解组：由意图分类和 Schema 选择节点设置 ───────────────────────

    intent: str  # 意图分类结果，取值为 "DataQuery" 或 "Other"
    # 设置时机：classify_intent 节点
    # 读取节点：route_by_intent（条件路由函数，决定走数据查询还是误导处理分支）
    # 决定流程走向：DataQuery → schema_selection → ... → 结果；Other → misleading → 结束

    schema_context: str  # LLM 精选后的数据库表结构描述文本
    # 设置时机：schema_selection 节点
    # 读取节点：generate_sql（作为 SQL 生成的上下文）, execute_sql（用于 SQL 自愈）
    # 内容格式：包含表名、字段名、字段类型、关联关系等，如：
    #   "可用的数据库表结构：\n表名: t_orders\n字段:\n  - id (INT) [主键]\n  - ..."

    raw_metadata: str  # 数据源的完整元数据 JSON 字符串（未经过 LLM 筛选）
    # 设置时机：schema_selection 节点
    # 读取节点：generate_sql（当精选 schema 生成 SQL 失败时，用完整元数据重试）
    # 用途：兜底策略 —— 如果 LLM 精选的 schema 太少导致生成失败，
    #       generate_sql 会用 raw_metadata 构建完整 schema 重新生成

    # ── 生成组：由 SQL 生成节点设置 ───────────────────────────────────

    sql: str  # 生成的 SQL 查询语句
    # 设置时机：generate_sql 节点（初次生成）, execute_sql 节点（自愈修复后可能更新）
    # 读取节点：execute_sql（执行查询）, self_heal（修复失败 SQL）
    # 空字符串 "" 表示生成失败

    table_fixes: list[str]  # SQL 表名自动修复记录，如 ["t_order -> t_orders"]
    # 设置时机：generate_sql 节点（表名校验修复时）
    # 读取节点：前端展示（告诉用户 SQL 做了哪些自动修正）
    # 用途：LLM 可能"幻觉"出不存在的表名，系统用相似度匹配自动替换

    column_fixes: list[str]  # SQL 列名自动修复记录，如 ["created_at -> order_time"]
    # 设置时机：generate_sql 节点（列名校验修复时）
    # 读取节点：前端展示
    # 用途：LLM 常见幻觉 —— 凭空编造 created_at 列，系统自动替换为真实的时间列

    # ── 结果组：由 SQL 执行节点设置 ───────────────────────────────────

    error: str  # 错误信息，成功时为 None
    # 设置时机：execute_sql 节点（执行失败时）, handle_misleading 节点, generate_sql 节点
    # 读取节点：前端展示（向用户显示友好的错误提示）
    # 典型值："SQL 执行失败: ..." 或 "抱歉，我无法理解您的问题..."

    columns: list[str]  # 查询结果的列名列表，如 ["月份", "销售额"]
    # 设置时机：execute_sql 节点
    # 读取节点：前端表格/图表渲染
    # 空列表 [] 表示无结果或执行失败

    rows: list[dict]  # 查询结果的数据行，每行是一个字典 {列名: 值}
    # 设置时机：execute_sql 节点
    # 读取节点：前端表格/图表渲染, chart_type 推断
    # 示例: [{"月份": "2024-01", "销售额": 15000}, ...]

    row_count: int  # 结果行数
    # 设置时机：execute_sql 节点
    # 读取节点：前端展示（"共 N 条记录"）
    # 注意：如果结果超过 query_max_rows 会被截断，row_count 是截断后的数量

    execution_time_ms: int  # SQL 执行耗时（毫秒）
    # 设置时机：execute_sql 节点
    # 读取节点：前端展示（"查询耗时 N ms"）
    # 用途：性能监控和用户反馈

    success: bool  # 查询是否成功
    # 设置时机：execute_sql 节点, handle_misleading 节点, generate_sql 节点
    # 读取节点：前端判断展示成功结果还是错误信息
    # True = 成功返回数据，False = 任何环节失败

    chart_type: str  # 推断的图表类型
    # 设置时机：execute_sql 节点（通过 chart_type.infer_chart_type() 推断）
    # 读取节点：前端图表组件决定渲染方式
    # 可能的值: "metric"(指标卡), "line"(折线图), "bar"(柱状图),
    #          "pie"(饼图), "grouped_bar"(分组柱状图), "scatter"(散点图), "table"(表格), "none"(无)
