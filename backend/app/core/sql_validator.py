"""
T030: SQL 三层校验 (AST + 危险函数 + 白名单列)

对标:
  - SEC-002 (openspec spec): 三层校验, 所有 SQL 执行前必须通过
  - v1 教训 #46: 必须用 AST 不能用字符串前缀
    (v1 validate_sql_safety 只 isinstance(stmt, Select), 被绕过)
  - Claude Code §3.3 Fail-Closed: 默认拒绝, 显式声明安全才放行

三层校验:
  Layer 1 (AST): sqlglot parse, 拒绝非 SELECT (DROP/DELETE/UPDATE/INSERT/...)
  Layer 2 (危险函数): 拒绝 LOAD_FILE/INTO OUTFILE/INTO DUMPFILE/SLEEP/BENCHMARK
  Layer 3 (白名单列): SQL 列名必须在语义层定义的列集合内

自愈后的 SQL 也走同样的三层校验 (v1 教训 #32: 自愈 prompt 无安全约束 → 能出 DROP)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)


# 危险函数黑名单 (对标 spec SEC-002 + v1 教训 #46)
# 这些函数可读文件/写文件/制造 DoS, SELECT 里出现即拒绝
_DANGEROUS_FUNCTIONS = frozenset({
    "LOAD_FILE",      # 读任意文件
    "SLEEP",          # DoS
    "BENCHMARK",      # DoS
    "GET_LOCK",       # DoS / 死锁
    "RELEASE_LOCK",   # 配合 GET_LOCK
})

# 危险子句 (INTO OUTFILE / INTO DUMPFILE 写文件)
_DANGEROUS_INTO = frozenset({"OUTFILE", "DUMPFILE"})


@dataclass
class ValidationResult:
    """校验结果。

    ok=True 通过; ok=False 拒绝 (reason + violated_layer 说明哪层)。
    """
    ok: bool
    reason: str = ""
    violated_layer: str = ""  # "AST" / "dangerous_function" / "whitelist_column"


def validate_sql(sql: str | None, allowed_columns: set[str] | None = None) -> ValidationResult:
    """三层校验 SQL。

    Args:
        sql: 待校验 SQL
        allowed_columns: 语义层定义的合法列名集合 (Layer 3 白名单)

    Returns:
        ValidationResult — 任一层失败即拒绝, reason 说明原因

    设计 (Fail-Closed, 对标 Claude Code §3.3):
      - parse 失败 → 拒绝 (宁可错杀, 不放行可疑 SQL)
      - 多语句 → 拒绝 (SQL 注入常见手法)
      - 默认拒绝, 只有显式通过三层才放行
    """
    if not sql or not sql.strip():
        return ValidationResult(ok=False, reason="空 SQL", violated_layer="AST")

    allowed_columns = allowed_columns or set()

    # ── Layer 1: AST 解析 + 语句类型 ──────────────────────────
    try:
        # parse 会返回所有语句; 多语句是 SQL 注入常见手法, 拒绝
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as e:
        logger.warning("SQL parse 失败 (语法错误): %s", e)
        return ValidationResult(
            ok=False,
            reason=f"SQL 语法错误, 无法解析: {e}",
            violated_layer="AST",
        )

    # 过滤掉 None (空语句) 后, 必须 exactly 1 条
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return ValidationResult(
            ok=False,
            reason=f"禁止多语句拼接 (检测到 {len(statements)} 条语句)",
            violated_layer="AST",
        )

    stmt = statements[0]

    # 必须是 SELECT (含 WITH CTE / 子查询, 它们顶层仍是 Select)
    if not isinstance(stmt, exp.Select):
        stmt_type = type(stmt).__name__
        return ValidationResult(
            ok=False,
            reason=f"仅允许 SELECT 语句, 检测到 {stmt_type}",
            violated_layer="AST",
        )

    # ── Layer 2: 危险函数 ─────────────────────────────────────
    # 遍历 AST 找所有函数调用
    # 注意: sqlglot 对未注册函数 (SLEEP/LOAD_FILE/BENCHMARK) 用 Anonymous 表示,
    # sql_name() 返回 "ANONYMOUS", 真实名字在 .name 属性里
    for func in stmt.find_all(exp.Func):
        # 优先用 Anonymous.name (真实函数名), 否则用 sql_name
        if isinstance(func, exp.Anonymous):
            func_name = func.name.upper()
        else:
            func_name = func.sql_name().upper() if hasattr(func, "sql_name") else ""

        if func_name in _DANGEROUS_FUNCTIONS:
            return ValidationResult(
                ok=False,
                reason=f"禁止使用危险函数: {func_name}",
                violated_layer="dangerous_function",
            )

    # 检查 INTO OUTFILE / INTO DUMPFILE (sqlglot 里是 exp.IntoProperty 或类似)
    sql_upper = sql.upper()
    for dangerous in _DANGEROUS_INTO:
        if f"INTO {dangerous}" in sql_upper:
            return ValidationResult(
                ok=False,
                reason=f"禁止使用 {dangerous} (可写文件)",
                violated_layer="dangerous_function",
            )

    # ── Layer 3: 白名单列 ─────────────────────────────────────
    if allowed_columns:
        # 提取 SELECT 别名 (AS xxx), 加入白名单 (ORDER BY/GROUP BY 引用别名是合法的)
        select_aliases = set()
        for alias_node in stmt.find_all(exp.Alias):
            if alias_node.alias:
                select_aliases.add(alias_node.alias)
        effective_whitelist = allowed_columns | select_aliases

        # 提取 SQL 里所有列引用 (Column 节点)
        # SELECT * 无法静态分析列, 跳过 (由 Layer 1/2 兜底)
        for col in stmt.find_all(exp.Column):
            col_name = col.name
            if not col_name:
                continue
            # 忽略通配符和函数参数里的伪列
            if col_name in ("*",):
                continue
            if col_name not in effective_whitelist:
                return ValidationResult(
                    ok=False,
                    reason=f"列 '{col_name}' 不在语义层白名单内",
                    violated_layer="whitelist_column",
                )

    return ValidationResult(ok=True)
