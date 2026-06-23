"""
T033: 结果自检 — 0行/异常数字/不一致 检测

对标:
  - AEE-003 (openspec spec): 成功执行后自检结果
    0行 → 过滤过严; 异常大数字 → 笛卡尔积/错误聚合; 全 NULL → JOIN 方向错
  - 宁缺毋滥: 异常结果不直接展示, 分析 → 自动修复 → 仍异常 ask_user

检测维度 (对标 spec AEE-003):
  1. ZERO_ROWS: 0 行结果 (过滤条件过严/逻辑错误)
  2. CARTESIAN_PRODUCT: 行数异常多 (缺 JOIN 条件)
  3. ALL_NULL: 结果全 NULL (JOIN 方向错/外键错)
  4. SUSPICIOUS_ZERO: COUNT(*)=0 且非空结果 (可疑)

设计:
  - check_result(rows, columns, sql) → CheckResult(ok, issue, reason, suggestion)
  - 纯规则检测 (不调 LLM), 快速 + 确定性
  - 异常 → suggestion 附修复方向 (供 T025 决策 ask_user 或自动修复)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ResultIssue(str, Enum):
    """结果异常类型。"""
    ZERO_ROWS = "ZERO_ROWS"
    CARTESIAN_PRODUCT = "CARTESIAN_PRODUCT"
    ALL_NULL = "ALL_NULL"
    SUSPICIOUS_ZERO = "SUSPICIOUS_ZERO"


@dataclass
class CheckResult:
    """自检结果。

    ok=True 通过; ok=False 异常 (issue + reason + suggestion 供 T025 决策)。
    """
    ok: bool
    issue: ResultIssue | None = None
    reason: str = ""
    suggestion: str = ""


# 笛卡尔积启发式: 无 JOIN ON 的多表查询 + 行数爆炸
# 阈值: 行数 > 500 且 SQL 有逗号连接的多表 (FROM a, b) 视为可疑
_CARTESIAN_ROW_THRESHOLD = 500


def _has_implicit_join(sql: str) -> bool:
    """检测 SQL 是否用隐式 JOIN (FROM a, b 而非 JOIN)。

    隐式 JOIN 容易遗漏 ON 条件 → 笛卡尔积。
    """
    sql_upper = sql.upper()
    # FROM a, b (逗号分隔多表) 且无 JOIN/WHERE 关联
    from_match = re.search(r"FROM\s+\w+\s*,\s*\w+", sql_upper)
    has_join = "JOIN" in sql_upper
    return bool(from_match) and not has_join


def _count_all_null_columns(rows: list[tuple]) -> int:
    """统计有多少列是全部 NULL。

    返回全 NULL 的列数 (0=没有全空列)。
    用于判断 JOIN 方向错 (右表多列全空), 单列全空不算异常。
    """
    if not rows:
        return 0
    n_cols = len(rows[0])
    if n_cols == 0:
        return 0
    null_cols = 0
    for col_idx in range(n_cols):
        if all(row[col_idx] is None for row in rows):
            null_cols += 1
    return null_cols


def _is_count_zero(rows: list[tuple], sql: str) -> bool:
    """是否是 COUNT(*)=0 的单行结果 (WHERE 过滤后可疑)。"""
    if len(rows) != 1:
        return False
    sql_upper = sql.upper()
    if "COUNT" not in sql_upper:
        return False
    val = rows[0][-1] if rows[0] else None
    return val == 0


def check_result(
    rows: list[tuple],
    columns: list[str],
    sql: str,
) -> CheckResult:
    """自检执行结果 (纯规则, 不调 LLM)。

    Args:
        rows: 执行结果行
        columns: 列名
        sql: 生成结果的 SQL (用于启发式分析)

    Returns:
        CheckResult — ok=True 正常; ok=False 异常 + suggestion 修复方向
    """
    # 1. 0 行
    if len(rows) == 0:
        return CheckResult(
            ok=False,
            issue=ResultIssue.ZERO_ROWS,
            reason="查询返回 0 行, 可能过滤条件过严或逻辑错误",
            suggestion="检查 WHERE 条件是否过严, 或换一个查询维度",
        )

    # 2. 全 NULL (仅 JOIN 场景有意义; 单表某列可空是正常数据)
    sql_upper = sql.upper()
    has_join = "JOIN" in sql_upper
    if has_join:
        null_col_count = _count_all_null_columns(rows)
        # 多列全 NULL 才报 (单列全 NULL 可能只是该字段普遍为空, 如 remark)
        if null_col_count >= 2:
            return CheckResult(
                ok=False,
                issue=ResultIssue.ALL_NULL,
                reason=f"结果中 {null_col_count} 列全部为 NULL, 可能 JOIN 方向错误或外键不匹配",
                suggestion="检查 JOIN 的表和外键方向, 可能需要换 JOIN 方向 (LEFT↔RIGHT)",
            )

    # 3. 笛卡尔积 (行数爆炸 + 隐式 JOIN)
    if _has_implicit_join(sql) and len(rows) > _CARTESIAN_ROW_THRESHOLD:
        return CheckResult(
            ok=False,
            issue=ResultIssue.CARTESIAN_PRODUCT,
            reason=f"结果 {len(rows)} 行, 疑似笛卡尔积 (缺少 JOIN 关联条件)",
            suggestion="添加 JOIN ON 关联条件, 避免表的全连接",
        )

    # 4. COUNT=0 可疑 (非空结果但聚合值是 0)
    if _is_count_zero(rows, sql):
        return CheckResult(
            ok=False,
            issue=ResultIssue.SUSPICIOUS_ZERO,
            reason="COUNT 结果为 0, WHERE 过滤后可能无匹配数据",
            suggestion="确认过滤条件是否正确, 或数据是否符合预期",
        )

    # 正常
    return CheckResult(ok=True)
