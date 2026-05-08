# =============================================================================
# SQL 生成提示词模板
# =============================================================================
#
# 【文件用途】
#   定义 NL→SQL 管道中 LLM 的提示词（prompt），包括系统角色指令和用户问题模板。
#   本文件是 generation.py（SQL 生成节点）的"提示词配置层"，二者关系：
#     generation.py  →  导入 SYSTEM_PROMPT / build_user_prompt / build_semantic_prompt
#                      →  组装 ChatML 消息列表 [("system", ...), ("human", ...)]
#                      →  调用 LLM 生成 SQL
#
# 【核心概念】
#   1. SYSTEM_PROMPT  — 系统角色提示词，定义 LLM 的行为边界和安全规则
#   2. build_user_prompt()  — 基础用户提示词，拼接 schema + 问题
#   3. build_semantic_prompt() — 增强版用户提示词，额外注入语义分析结果
#      （intent / metric / dimensions / filters / time_range / sort / limit）
#
# 【设计要点】
#   - 提示词与代码分离：修改提示词不需要动 generation.py 的逻辑
#   - 变量占位使用 Python f-string，运行时由 build_* 函数填充
#   - SYSTEM_PROMPT 中"时间字段规则"单独成段，因为这是 LLM 最容易犯的错误
# =============================================================================

# ---------------------------------------------------------------------------
# SYSTEM_PROMPT — 系统角色提示词
# ---------------------------------------------------------------------------
# 消耗方式：generation.py 中作为 ChatML 的 "system" 消息传入 LLM
#   messages = [("system", SYSTEM_PROMPT), ("human", build_user_prompt(...))]
#
# 结构拆解：
#   角色定义 → 通用规则(12条) → 时间字段规则(重点) → 输出格式约束
#
# 关键设计：
#   - 规则3/4：强制 LLM 严格使用 schema 中的表名/列名，防止"幻觉"
#   - 规则9：中文别名要求，让前端表格直接展示可读列名，无需二次映射
#   - 规则11：只输出 SQL，禁止解释和 markdown，简化下游解析
#   - 时间字段规则单独强调：LLM 常见错误是假设存在 created_at，必须用 schema 实际字段
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一个专业的 SQL 生成助手。你的任务根据用户的自然语言问题和提供的数据库结构，生成准确的 SQL 查询。

## 规则
1. 只生成 SELECT 语句，禁止任何修改操作（INSERT/UPDATE/DELETE/CREATE/ALTER/DROP）
2. 使用标准 SQL 语法，兼容 MySQL
3. 表名必须严格使用"可用表名"列表中提供的名称，不得增减或修改（如禁止将 t_orders 写为 orders）
4. 列名必须严格使用 schema 中提供的名称，不得臆造不存在的列
5. 如果问题涉及多个表，使用正确的 JOIN 关系
6. 对于聚合查询，使用 GROUP BY + HAVING
7. 对于排序查询，使用 ORDER BY，默认降序
8. 对于 Top N 查询，使用 LIMIT，默认 100 条
9. 每个查询字段必须使用中文别名（AS '中文名'），别名应简洁易懂，优先使用字段注释中的中文名。聚合字段也要有中文别名，如 COUNT(*) AS '数量', AVG(price) AS '平均价格'
10. 禁止使用 CREATE TEMPORARY TABLE、子查询中的 DDL 等结构
11. 只返回 SQL 语句本身，不要解释、不要 markdown 代码块
12. 如果问题无法直接映射到表结构，尝试使用最相关的表和通用聚合函数，不要返回空

## 时间字段规则（最重要！）
- 当用户提到"最近N天/本周/本月"等时间条件时，必须先在 schema 中找到实际的时间字段名
- 常见时间字段名：created_at, updated_at, timestamp, recorded_at, start_time, date 等
- 绝对禁止假设存在 created_at 字段！必须使用 schema 中实际列出的时间字段
- 如果 schema 中没有时间字段，则不要添加时间过滤条件

## 输出格式
仅输出一条 SQL 语句，以分号结尾。"""

# ---------------------------------------------------------------------------
# build_user_prompt — 基础用户提示词构建
# ---------------------------------------------------------------------------
# 将数据库 schema 和用户问题拼接为 LLM 的 "human" 消息内容。
#
# 参数:
#   question (str)       — 用户的自然语言问题，如"最近7天的订单金额"
#   schema_context (str) — 由 schema 选择节点提供的表结构文本，
#                          格式示例："表名: t_orders\n列: id(INT), amount(DECIMAL), ..."
#
# 返回:
#   str — 拼接好的提示词文本
#
# Python 提示:
#   f-string 三引号保留换行，{schema_context} 和 {question} 在运行时替换
# ---------------------------------------------------------------------------


def build_user_prompt(question: str, schema_context: str) -> str:
    return f"""数据库结构：
{schema_context}

问题：{question}

请生成对应的 SQL 查询语句。"""


# ---------------------------------------------------------------------------
# build_semantic_prompt — 含语义分析结果的增强提示词
# ---------------------------------------------------------------------------
# 在基础 schema + question 之上，额外注入语义分析节点的结构化结果，
# 让 LLM 在生成 SQL 时有更明确的"意图信号"，减少歧义。
#
# 当前状态：已在 generation.py 中导入，但尚未在主流程中启用。
# 未来语义分析路径打通后，将替代 build_user_prompt 作为主要提示词。
#
# 参数:
#   question (str)       — 用户自然语言问题
#   schema_context (str) — 数据库 schema 文本
#   semantics (dict)     — 语义分析结果，可能包含以下键：
#     - intent (str):      查询意图，如 "DataQuery", "Aggregation"
#     - metric (dict):     聚合指标 {function, column}，如 {"function":"SUM","column":"amount"}
#     - dimensions (list): 分组维度 [{"column":"category"}, ...]
#     - filters (list):    过滤条件 [{"column":"status","operator":"=","value":"paid"}, ...]
#     - time_range (dict): 时间范围 {relative, start, end}
#     - sort (dict):       排序 {column, order}
#     - limit (int):       行数限制
#
# 返回:
#   str — 拼接好的增强提示词文本
#
# 设计要点:
#   - semantics 为空时退化为基础 prompt，保证向后兼容
#   - filter 中的单引号用 replace("'", "''") 转义，防止 SQL 注入风险
#   - 使用 parts 列表 + "\n".join() 而非连续字符串拼接，便于条件组装
# ---------------------------------------------------------------------------


def build_semantic_prompt(
    question: str,
    schema_context: str,
    semantics: dict,
) -> str:
    """构建包含语义分析结果的 prompt。"""
    parts = [f"数据库结构：\n{schema_context}", f"\n问题：{question}"]

    if semantics:
        parts.append("\n## 语义分析结果")
        # 默认意图为 DataQuery，语义分析未识别时兜底
        intent = semantics.get("intent") or "DataQuery"
        parts.append(f"- 查询类型: {intent}")

        metric = semantics.get("metric")
        if metric:
            func = metric.get("function", "SELECT")  # 默认 SELECT，无聚合时的兜底
            col = metric.get("column", "?")
            parts.append(f"- 指标: {func}({col})")

        dims = semantics.get("dimensions")
        if dims:
            # 生成器表达式提取维度列名，比列表推导更省内存（维度数量少时差异不大）
            cols = ", ".join(d["column"] for d in dims)
            parts.append(f"- 分组维度: {cols}")

        filters = semantics.get("filters")
        if filters:
            conditions = []
            for f in filters:
                col = f.get("column", "?")
                op = f.get("operator", "=")
                # 单引号转义：防止值中包含 ' 时破坏 SQL 语法（如 O'Brien → O''Brien）
                val = str(f.get("value", "?")).replace("'", "''")
                conditions.append(f"{col} {op} '{val}'")
            parts.append(f"- 过滤条件: {' AND '.join(conditions)}")

        tr = semantics.get("time_range")
        if tr:
            parts.append(f"- 时间范围: {tr.get('relative', '')} → {tr.get('start', '')} ~ {tr.get('end', '')}")

        sort = semantics.get("sort")
        if sort:
            parts.append(f"- 排序: {sort.get('column', '')} {sort.get('order', 'DESC')}")

        limit = semantics.get("limit")
        if limit:
            parts.append(f"- 限制: LIMIT {limit}")

    parts.append("\n请根据以上语义分析结果生成对应的 SQL 查询语句。")
    return "\n".join(parts)
