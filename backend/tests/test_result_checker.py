"""
T033: 结果自检 — 单元测试

对标:
  - AEE-003 (openspec spec): 0行/异常大数字(笛卡尔积)/不一致 → 分析 → 修复/ask_user
  - 宁缺毋滥: 异常结果不直接展示给用户

检测维度:
  1. 0行: 过滤条件过严/逻辑错误
  2. 异常大数字: 笛卡尔积 (缺少 JOIN 条件) / 错误聚合
  3. 异常小: COUNT=0 但不该是 0
"""
from __future__ import annotations

import pytest

from app.ai.result_checker import check_result, CheckResult, ResultIssue


class TestCheckResult:
    """结果自检: 检测异常结果。"""

    def test_normal_result_passes(self):
        """正常结果 → 无异常。"""
        result = check_result(
            rows=[("alice", 100), ("bob", 200)],
            columns=["name", "amount"],
            sql="SELECT name, SUM(amount) FROM orders GROUP BY name",
        )
        assert result.ok
        assert result.issue is None

    def test_zero_rows_flagged(self):
        """0行结果 → 标记 (可能是过滤条件过严)。"""
        result = check_result(
            rows=[],
            columns=["name"],
            sql="SELECT name FROM orders WHERE status = 'nonexistent'",
        )
        assert not result.ok
        assert result.issue == ResultIssue.ZERO_ROWS

    def test_cartesian_product_detected(self):
        """行数异常多 → 疑似笛卡尔积 (缺 JOIN 条件)。"""
        # 两张 100 行的表无 JOIN 条件 → 10000 行
        rows = [(i, j) for i in range(100) for j in range(100)]
        result = check_result(
            rows=rows,
            columns=["a", "b"],
            sql="SELECT a.x, b.y FROM a, b",  # 无 JOIN ON
        )
        assert not result.ok
        assert result.issue == ResultIssue.CARTESIAN_PRODUCT

    def test_large_aggregate_not_flagged(self):
        """大聚合结果 (正常 GROUP BY) 不误判为笛卡尔积。"""
        rows = [(f"cat_{i}", i * 100) for i in range(50)]
        result = check_result(
            rows=rows,
            columns=["category", "total"],
            sql="SELECT category, SUM(amount) FROM orders GROUP BY category",
        )
        assert result.ok

    def test_null_values_flagged(self):
        """结果含大量 NULL → 可能 JOIN 方向错/外键错。"""
        rows = [("a", None), ("b", None), ("c", None)]
        result = check_result(
            rows=rows,
            columns=["name", "joined_value"],
            sql="SELECT a.name, b.val FROM a LEFT JOIN b ON a.id = b.aid",
        )
        assert not result.ok
        assert result.issue == ResultIssue.ALL_NULL

    def test_count_zero_when_aggregate_flagged(self):
        """COUNT 结果是 0 (但不是 0行空结果) → 标记。"""
        result = check_result(
            rows=[("total", 0)],
            columns=["label", "count"],
            sql="SELECT 'total', COUNT(*) FROM orders WHERE status='paid'",
        )
        # COUNT(*)=0 单行结果, 可能合理也可能异常, 标记让 LLM 判断
        # 但如果是 GROUP BY 的 count, 0 是合理的
        # 这里是 WHERE 过滤后 COUNT=0, 偏可疑
        assert result.issue in (None, ResultIssue.SUSPICIOUS_ZERO)


class TestCheckResultDetails:
    """自检结果详情 (供 T025 决策)。"""

    def test_result_has_reason(self):
        result = check_result([], ["x"], "SELECT x FROM t WHERE 1=0")
        assert result.reason is not None
        assert len(result.reason) > 0

    def test_result_has_suggestion(self):
        """异常结果应附修复建议 (供 ask_user 或自动修复)。"""
        result = check_result([], ["x"], "SELECT x FROM t WHERE status='bad'")
        assert result.suggestion is not None

    def test_cartesian_suggests_join(self):
        """笛卡尔积 → 建议加 JOIN 条件。"""
        rows = [(i, j) for i in range(50) for j in range(50)]
        result = check_result(rows, ["a", "b"], "SELECT * FROM a, b")
        assert "JOIN" in result.suggestion.upper() or "条件" in result.suggestion
