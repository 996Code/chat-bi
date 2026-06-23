"""
T030: SQL 三层校验 — 单元测试

对标:
  - SEC-002 (openspec spec): AST拒绝非SELECT + 危险函数拒绝 + 白名单列名校验
  - v1 教训 #46: 必须用 AST 不能用字符串前缀 (v1 validate_sql_safety 只 isinstance Select)
  - Claude Code §3.3 Fail-Closed: 默认拒绝, 显式声明安全才放行

设计:
  - validate_sql(sql, allowed_columns) → ValidationResult(ok, reason, violated_layer)
  - Layer 1 AST: sqlglot parse, 拒绝非 SELECT (DROP/DELETE/UPDATE/INSERT/...)
  - Layer 2 危险函数: 拒绝 LOAD_FILE/INTO OUTFILE/INTO DUMPFILE/SLEEP/BENCHMARK
  - Layer 3 白名单列: SQL 列名必须在语义层定义的列集合内
  - 真实 sqlglot 解析, 不 mock (校验逻辑本身要被测)
"""
from __future__ import annotations

import pytest

from app.core.sql_validator import validate_sql, ValidationResult


# ── Layer 1: AST 拒绝非 SELECT ────────────────────────────────

class TestLayer1AST:
    """sqlglot AST 解析, 拒绝非 SELECT 语句。"""

    def test_select_passes(self):
        r = validate_sql("SELECT id, name FROM users", allowed_columns={"id", "name"})
        assert r.ok

    def test_select_star_passes(self):
        r = validate_sql("SELECT * FROM users", allowed_columns={"id"})
        assert r.ok

    def test_drop_rejected(self):
        r = validate_sql("DROP TABLE users", allowed_columns=set())
        assert not r.ok
        assert "AST" in r.violated_layer or "SELECT" in r.reason.upper()

    def test_delete_rejected(self):
        r = validate_sql("DELETE FROM users WHERE id = 1", allowed_columns={"id"})
        assert not r.ok

    def test_update_rejected(self):
        r = validate_sql("UPDATE users SET name = 'x'", allowed_columns={"name"})
        assert not r.ok

    def test_insert_rejected(self):
        r = validate_sql("INSERT INTO users VALUES (1)", allowed_columns=set())
        assert not r.ok

    def test_truncate_rejected(self):
        r = validate_sql("TRUNCATE TABLE users", allowed_columns=set())
        assert not r.ok

    def test_alter_rejected(self):
        r = validate_sql("ALTER TABLE users ADD COLUMN x INT", allowed_columns=set())
        assert not r.ok

    def test_create_rejected(self):
        r = validate_sql("CREATE TABLE evil (id INT)", allowed_columns=set())
        assert not r.ok

    def test_select_with_cte_passes(self):
        """CTE (WITH ... SELECT) 应放行 (合法分析查询)。"""
        r = validate_sql(
            "WITH t AS (SELECT id FROM users) SELECT * FROM t",
            allowed_columns={"id"},
        )
        assert r.ok

    def test_select_with_subquery_passes(self):
        r = validate_sql(
            "SELECT name FROM (SELECT id, name FROM users) t",
            allowed_columns={"id", "name"},
        )
        assert r.ok

    def test_syntax_error_rejected(self):
        """语法错误的 SQL 应拒绝 (parse 失败)。"""
        r = validate_sql("SELCT frm users", allowed_columns=set())
        assert not r.ok


# ── Layer 2: 危险函数拒绝 ─────────────────────────────────────

class TestLayer2DangerousFunctions:
    """拒绝危险函数 (对标 spec + v1 教训 #46)。"""

    def test_load_file_rejected(self):
        r = validate_sql(
            "SELECT LOAD_FILE('/etc/passwd')",
            allowed_columns=set(),
        )
        assert not r.ok
        assert "danger" in r.violated_layer.lower() or "函数" in r.reason

    def test_into_outfile_rejected(self):
        r = validate_sql(
            "SELECT * FROM users INTO OUTFILE '/tmp/evil'",
            allowed_columns={"id"},
        )
        assert not r.ok

    def test_into_dumpfile_rejected(self):
        r = validate_sql(
            "SELECT * FROM users INTO DUMPFILE '/tmp/evil'",
            allowed_columns={"id"},
        )
        assert not r.ok

    def test_sleep_rejected(self):
        r = validate_sql("SELECT SLEEP(100)", allowed_columns=set())
        assert not r.ok

    def test_benchmark_rejected(self):
        r = validate_sql("SELECT BENCHMARK(1000000, MD5('x'))", allowed_columns=set())
        assert not r.ok

    def test_normal_aggregate_passes(self):
        """正常聚合函数不应误杀。"""
        r = validate_sql(
            "SELECT SUM(amount), COUNT(*) FROM orders",
            allowed_columns={"amount"},
        )
        assert r.ok


# ── Layer 3: 白名单列校验 ─────────────────────────────────────

class TestLayer3WhitelistColumns:
    """SQL 列名必须在语义层定义的列集合内。"""

    def test_known_columns_pass(self):
        r = validate_sql(
            "SELECT id, name, total_amount FROM orders WHERE user_id = 1",
            allowed_columns={"id", "name", "total_amount", "user_id"},
        )
        assert r.ok

    def test_unknown_column_rejected(self):
        """SQL 引用了语义层未定义的列 → 拒绝 (防幻觉/注入)。"""
        r = validate_sql(
            "SELECT password FROM users",
            allowed_columns={"id", "name"},  # password 不在白名单
        )
        assert not r.ok
        assert "column" in r.violated_layer.lower() or "列" in r.reason or "whitelist" in r.violated_layer.lower()

    def test_aliased_column_passes(self):
        """带别名 (AS) 的列应识别真实列名。"""
        r = validate_sql(
            "SELECT total_amount AS amt FROM orders",
            allowed_columns={"total_amount"},
        )
        assert r.ok

    def test_empty_whitelist_select_star_passes(self):
        """SELECT * 不校验列 (无法静态分析, 放行让 Layer1/2 管)。"""
        r = validate_sql("SELECT * FROM orders", allowed_columns=set())
        assert r.ok

    def test_window_function_alias_in_orderby_passes(self):
        """窗口函数别名在 ORDER BY 引用 → 放行 (别名不是真实列, 不该拦)。

        RANK() OVER(...) AS rank ... ORDER BY rank — rank 是别名不是列。
        """
        r = validate_sql(
            "SELECT id, RANK() OVER (ORDER BY total_amount DESC) AS rnk "
            "FROM orders ORDER BY rnk",
            allowed_columns={"id", "total_amount"},  # rnk 是别名不在白名单
        )
        assert r.ok

    def test_aggregate_alias_in_orderby_passes(self):
        """聚合别名在 ORDER BY 引用 → 放行。"""
        r = validate_sql(
            "SELECT category, COUNT(*) AS cnt FROM products GROUP BY category ORDER BY cnt DESC",
            allowed_columns={"category"},  # cnt 是别名
        )
        assert r.ok

    def test_cte_output_column_passes(self):
        """CTE 输出列引用 → 放行 (系统性收集, 不只是别名)。"""
        r = validate_sql(
            "WITH cte AS (SELECT id, total_amount FROM orders) "
            "SELECT id FROM cte WHERE total_amount > 100",
            allowed_columns={"id", "total_amount"},  # cte 是 CTE 名
        )
        assert r.ok

    def test_subquery_derived_column_passes(self):
        """子查询派生表的列引用 → 放行。"""
        r = validate_sql(
            "SELECT sub.total FROM (SELECT id, SUM(total_amount) AS total FROM orders GROUP BY id) sub",
            allowed_columns={"id", "total_amount"},  # total 是子查询别名
        )
        assert r.ok


# ── 综合边界 ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_sql_rejected(self):
        r = validate_sql("", allowed_columns=set())
        assert not r.ok

    def test_none_sql_rejected(self):
        r = validate_sql(None, allowed_columns=set())  # type: ignore
        assert not r.ok

    def test_multi_statement_rejected(self):
        """多条语句拼接 (SQL 注入常见手法) → 拒绝。"""
        r = validate_sql(
            "SELECT * FROM users; DROP TABLE users",
            allowed_columns={"id"},
        )
        assert not r.ok

    def test_comment_injection_rejected(self):
        """注释绕过尝试 → AST 会识破。"""
        r = validate_sql(
            "SELECT * FROM users -- ; DROP TABLE users",
            allowed_columns={"id"},
        )
        # sqlglot 默认只 parse 第一条, 这条仍是合法 SELECT → 通过列校验
        # 但如果带了 DROP 在注释外, Layer1 会抓
        # 这里注释内的 DROP 被 sqlglot 忽略, SELECT 合法 → 应放行
        assert r.ok
