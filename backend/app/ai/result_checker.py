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
    """自检执行结果 (基于统计特征, 不依赖 SQL 文本模式)。

    设计原则: 异常判断基于结果本身的数据特征, 不解析 SQL 文本。
    原因: SQL 文本分析脆弱 (大小写/子查询/CTE/格式差异), 且 T030 已做 AST 校验,
    结果自检应独立判断"结果是否合理", 不重复 SQL 层面的工作。

    Args:
        rows: 执行结果行
        columns: 列名
        sql: 原始 SQL (仅用于 suggestion 提示, 不参与异常判断逻辑)

    Returns:
        CheckResult — ok=True 正常; ok=False 异常 + suggestion
    """
    n_rows = len(rows)
    n_cols = len(columns)

    # 1. 0 行 (明确异常; COUNT 返回 0 单独处理)
    if n_rows == 0:
        return CheckResult(
            ok=False,
            issue=ResultIssue.ZERO_ROWS,
            reason="查询返回 0 行, 可能过滤条件过严或逻辑错误",
            suggestion="检查 WHERE 条件是否过严, 或换一个查询维度",
        )

    # 2. 多数列全空 (JOIN 完全没匹配上的统计特征)
    # 设计: 计算"全空列占比", 超过半数说明结果实质为空 (典型 JOIN 方向错)
    # 不依赖 SQL 是否含 JOIN 关键字 — 单表也可能因 CASE WHEN 等产生全空列
    if n_cols >= 2:
        null_col_count = _count_all_null_columns(rows)
        if null_col_count > n_cols / 2:
            return CheckResult(
                ok=False,
                issue=ResultIssue.ALL_NULL,
                reason=f"结果 {null_col_count}/{n_cols} 列全部为 NULL, 数据实质为空",
                suggestion="检查 JOIN 的表和外键方向, 或 WHERE 条件是否矛盾",
            )

    # 3. 行数异常多 (潜在笛卡尔积)
    # 设计: 用配置阈值, 不在代码写死; 超过即提示 (不阻断, 由调用方判断)
    from app.core.config import get_settings
    if n_rows > get_settings().sql_max_rows * 0.5:  # 超过 max_rows 一半
        return CheckResult(
            ok=False,
            issue=ResultIssue.CARTESIAN_PRODUCT,
            reason=f"结果 {n_rows} 行, 异常多, 疑似笛卡尔积或缺少过滤",
            suggestion="检查是否缺少 JOIN ON 条件或 WHERE 过滤",
        )

    # 4. COUNT(*)=0 单行结果 (WHERE 过滤后无匹配, 偏可疑但非必然异常)
    if _is_count_zero(rows, sql):
        return CheckResult(
            ok=False,
            issue=ResultIssue.SUSPICIOUS_ZERO,
            reason="COUNT 结果为 0, WHERE 过滤后可能无匹配数据",
            suggestion="确认过滤条件是否正确, 或数据是否符合预期",
        )

    # 正常
    return CheckResult(ok=True)
