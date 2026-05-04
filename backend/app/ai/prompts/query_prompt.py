SYSTEM_PROMPT = """你是一个专业的 SQL 生成助手。你的任务根据用户的自然语言问题和提供的数据库结构，生成准确的 SQL 查询。

## 规则
1. 只生成 SELECT 语句，禁止任何修改操作（INSERT/UPDATE/DELETE/CREATE/ALTER/DROP）
2. 使用标准 SQL 语法，兼容 MySQL
3. 表名必须严格使用"可用表名"列表中提供的名称，不得增减或修改（如禁止将 t_orders 写为 orders）
4. 列名必须严格使用 schema 中提供的名称，不得臆造
5. 如果问题涉及多个表，使用正确的 JOIN 关系
6. 对于聚合查询，使用 GROUP BY + HAVING
7. 对于排序查询，使用 ORDER BY，默认降序
8. 对于 Top N 查询，使用 LIMIT，默认 100 条
9. 禁止在 SQL 中使用中文别名和中文注释
10. 禁止使用 CREATE TEMPORARY TABLE、子查询中的 DDL 等结构
11. 只返回 SQL 语句本身，不要解释、不要 markdown 代码块
12. 如果问题无法直接映射到表结构，尝试使用最相关的表和通用聚合函数，不要返回空

## 输出格式
仅输出一条 SQL 语句，以分号结尾。"""


def build_user_prompt(question: str, schema_context: str) -> str:
    return f"""数据库结构：
{schema_context}

问题：{question}

请生成对应的 SQL 查询语句。"""


def build_semantic_prompt(
    question: str,
    schema_context: str,
    semantics: dict,
) -> str:
    """构建包含语义分析结果的 prompt。"""
    parts = [f"数据库结构：\n{schema_context}", f"\n问题：{question}"]

    if semantics:
        parts.append("\n## 语义分析结果")
        intent = semantics.get("intent") or "DataQuery"
        parts.append(f"- 查询类型: {intent}")

        metric = semantics.get("metric")
        if metric:
            func = metric.get("function", "SELECT")
            col = metric.get("column", "?")
            parts.append(f"- 指标: {func}({col})")

        dims = semantics.get("dimensions")
        if dims:
            cols = ", ".join(d["column"] for d in dims)
            parts.append(f"- 分组维度: {cols}")

        filters = semantics.get("filters")
        if filters:
            conditions = []
            for f in filters:
                col = f.get("column", "?")
                op = f.get("operator", "=")
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
