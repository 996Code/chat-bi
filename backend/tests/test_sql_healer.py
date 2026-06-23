"""
T032: SQL 自愈 — 单元测试

对标:
  - AEE-002 (openspec spec): 错误码映射 + 专项纠正 prompt + 2轮上限 + 熔断器
  - v1 教训 #32: 自愈 prompt 必须保留全部安全规则 (v1 只写"你只生成SQL" → 能出 DROP)
  - Claude Code §6.4: 熔断器 (连续失败停止, 不无限重试)

错误码 (spec AEE-002):
  1146 表不存在 / 1054 列不存在 / 1064 语法 / 1052 歧义列 ...
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.sql_healer import (
    SelfHealCircuitBreaker,
    heal_sql,
    extract_error_code,
    ErrorCategory,
)


# ── 错误码提取 ────────────────────────────────────────────────

class TestExtractErrorCode:
    """从执行错误信息提取错误码 (供映射)。"""

    def test_extract_mysql_code(self):
        code = extract_error_code("(1146, \"Table 'db.users' doesn't exist\")")
        assert code == "1146"

    def test_extract_pg_code(self):
        """PostgreSQL 错误无数字码 → 返回 None (类别靠文字推断)。"""
        code = extract_error_code('relation "userss" does not exist')
        # PG 无数字码, 返回 None (正确行为)
        assert code is None
        # 但 from_error_code 应能从错误类别处理 None
        assert ErrorCategory.from_error_code(None) == ErrorCategory.UNKNOWN

    def test_extract_column_error(self):
        code = extract_error_code("(1054, \"Unknown column 'passwrod' in 'field list'\")")
        assert code == "1054"

    def test_no_code_returns_none(self):
        assert extract_error_code("some random error") is None


# ── 错误类别映射 ──────────────────────────────────────────────

class TestErrorCategoryMapping:
    """错误码 → 类别 (决定纠正 prompt 方向)。"""

    def test_table_not_exist(self):
        assert ErrorCategory.from_error_code("1146") == ErrorCategory.TABLE_NOT_EXIST

    def test_column_not_exist(self):
        assert ErrorCategory.from_error_code("1054") == ErrorCategory.COLUMN_NOT_EXIST

    def test_syntax_error(self):
        assert ErrorCategory.from_error_code("1064") == ErrorCategory.SYNTAX_ERROR

    def test_ambiguous_column(self):
        assert ErrorCategory.from_error_code("1052") == ErrorCategory.AMBIGUOUS_COLUMN

    def test_unknown_code(self):
        assert ErrorCategory.from_error_code("9999") == ErrorCategory.UNKNOWN

    def test_none_code(self):
        assert ErrorCategory.from_error_code(None) == ErrorCategory.UNKNOWN


# ── heal_sql: 自愈流程 ────────────────────────────────────────

def _mock_llm(healed_sql: str = "SELECT id FROM orders"):
    fake = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = healed_sql
    fake.chat.completions.create = AsyncMock(return_value=resp)
    return fake


class TestHealSql:
    """heal_sql: 错误 → 专项纠正 prompt → 生成新 SQL → 走同样校验。"""

    @pytest.mark.asyncio
    async def test_heal_column_error_success(self):
        """列名错误 → 自愈生成正确列名 (走同样三层校验)。"""
        llm = _mock_llm("SELECT id FROM orders")  # 自愈后正确 SQL
        result = await heal_sql(
            sql="SELECT passwrod FROM orders",  # 列名拼错
            error="(1054, \"Unknown column 'passwrod'\")",
            allowed_columns={"id", "password", "name"},
            schema_context="orders(id, password, name)",
            llm_client=llm,
        )
        assert result.success
        assert "SELECT" in result.sql.upper()
        assert result.validation.ok

    @pytest.mark.asyncio
    async def test_heal_prompt_contains_security_rules(self):
        """自愈 prompt 必须保留全部安全规则 (v1 教训 #32 核心)。"""
        llm = _mock_llm("SELECT id FROM orders")
        await heal_sql(
            sql="SELECT bad FROM orders",
            error="(1054, \"Unknown column 'bad'\")",
            allowed_columns={"id", "name"},
            schema_context="orders(id, name)",
            llm_client=llm,
        )
        prompt = llm.chat.completions.create.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        # 安全规则必须在 (SELECT only + 白名单 + 危险函数)
        assert "SELECT" in full
        assert "禁止" in full or "只能" in full

    @pytest.mark.asyncio
    async def test_heal_prompt_contains_available_columns(self):
        """列缺失错误 → prompt 提供可用列 (纠正方向)。"""
        llm = _mock_llm("SELECT id FROM orders")
        await heal_sql(
            sql="SELECT bad FROM orders",
            error="(1054, \"Unknown column 'bad'\")",
            allowed_columns={"id", "name", "total_amount"},
            schema_context="orders(id, name, total_amount)",
            llm_client=llm,
        )
        prompt = llm.chat.completions.create.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        assert "total_amount" in full  # 可用列出现在 prompt

    @pytest.mark.asyncio
    async def test_healed_sql_goes_through_validation(self):
        """自愈后的 SQL 走同样三层校验 (v1 教训 #32: 不信任自愈结果)。"""
        llm = _mock_llm("DROP TABLE orders")  # 自愈生成了危险 SQL
        result = await heal_sql(
            sql="SELECT bad FROM orders",
            error="(1054, \"Unknown column 'bad'\")",
            allowed_columns={"id"},
            schema_context="orders(id)",
            llm_client=llm,
        )
        assert not result.success  # 校验拦截
        assert not result.validation.ok

    @pytest.mark.asyncio
    async def test_heal_llm_failure_returns_failure(self):
        """LLM 失败 → 返回失败 (不抛)。"""
        llm = MagicMock()
        llm.chat.completions.create = AsyncMock(side_effect=Exception("LLM down"))
        result = await heal_sql(
            sql="SELECT bad FROM t",
            error="(1054, \"Unknown column 'bad'\")",
            allowed_columns={"id"},
            schema_context="t(id)",
            llm_client=llm,
        )
        assert not result.success
        assert result.error is not None


# ── 熔断器 ────────────────────────────────────────────────────

class TestCircuitBreaker:
    """熔断器: 跨查询连续失败 N 次 → 停止自愈 (对标 Claude Code §6.4)。"""

    def test_below_threshold_not_tripped(self):
        cb = SelfHealCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_tripped()

    def test_at_threshold_tripped(self):
        cb = SelfHealCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_tripped()

    def test_success_resets(self):
        """自愈成功 → 重置计数 (失败不累积)。"""
        cb = SelfHealCircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert not cb.is_tripped()

    @pytest.mark.asyncio
    async def test_tripped_blocks_healing(self):
        """熔断后 → heal_sql 直接返回失败 (不调 LLM)。"""
        cb = SelfHealCircuitBreaker(threshold=1)
        cb.record_failure()
        assert cb.is_tripped()

        llm = _mock_llm("SELECT 1")
        result = await heal_sql(
            sql="SELECT bad", error="(1054)", allowed_columns={"id"},
            schema_context="t(id)", llm_client=llm, circuit_breaker=cb,
        )
        assert not result.success
        llm.chat.completions.create.assert_not_called()  # 熔断, 没调 LLM

    def test_reset_clears(self):
        cb = SelfHealCircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_tripped()
        cb.reset()
        assert not cb.is_tripped()
