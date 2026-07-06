"""
T029: SQL 生成 — 单元测试

对标:
  - AEE-001 (openspec spec): 生成 SQL + 白名单列 + data_type 约束 + Skills + 历史
  - Claude Code §4: Prompt 分层 (静态 schema/skills 可缓存, 动态问题/历史分离)
  - RAG-005: data_type 必须注入 prompt (防 SUM on VARCHAR)

设计:
  - generate_sql(question, schema_context, allowed_columns, ...) → GenerateResult
  - prompt 分层组装 (prompt_cache: 静态 schema/约束, 动态 问题/fewshot/历史)
  - 生成后立即调 T030 校验 (失败 → 返回校验错误, 不执行)
  - 白名单列 + data_type 约束注入 prompt
  - 失败降级: LLM 失败/解析失败 → error (不抛)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.sql_agent import generate_sql, GenerateResult


def _mock_llm_content(sql: str = "SELECT id FROM orders"):
    """返回 (content, mock_resp) 元组, 用于 patch llm_chat 的 return_value。"""
    mock_resp = MagicMock()
    mock_resp.usage = None
    return (sql, mock_resp)


# ── 生成成功 ──────────────────────────────────────────────────

class TestGenerateSuccess:
    """SQL 生成 + 校验。"""

    @pytest.mark.asyncio
    async def test_generate_valid_sql(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT id FROM orders")):
            result = await generate_sql(
                question="所有订单",
                schema_context="orders 表: id, total_amount, user_id",
                allowed_columns={"id", "total_amount", "user_id"},
            )
        assert result.error is None
        assert "SELECT" in result.sql.upper()
        assert result.validation.ok

    @pytest.mark.asyncio
    async def test_generate_returns_sql_only(self):
        """从 LLM 响应里提取纯 SQL (去掉 markdown ``` 包裹/解释文字)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("```sql\nSELECT id FROM orders\n```")):
            result = await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id"},
            )
        assert result.error is None
        assert "SELECT" in result.sql
        assert "```" not in result.sql


# ── 校验集成 ──────────────────────────────────────────────────

class TestValidationIntegration:
    """生成后立即走 T030 三层校验。"""

    @pytest.mark.asyncio
    async def test_dangerous_sql_rejected(self):
        """LLM 生成了危险 SQL → 校验拦截, error 标明。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("DROP TABLE orders")):
            result = await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id"},
            )
        assert result.error is not None
        assert not result.validation.ok

    @pytest.mark.asyncio
    async def test_unknown_column_rejected(self):
        """LLM 用了白名单外的列 → Layer3 拦截。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT password FROM users")):
            result = await generate_sql(
                question="用户",
                schema_context="users(id, name)",
                allowed_columns={"id", "name"},  # password 不在
            )
        assert result.error is not None
        assert not result.validation.ok


# ── Prompt 组装 (对标 Claude Code §4 分层) ────────────────────

class TestPromptAssembly:
    """prompt 分层: 静态 (schema/约束) + 动态 (问题/fewshot)。"""

    @pytest.mark.asyncio
    async def test_prompt_contains_schema(self):
        """prompt 必须含 schema_context (静态层)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT id FROM orders")) as mock_chat:
            await generate_sql(
                question="订单",
                schema_context="orders 表包含 id total_amount",
                allowed_columns={"id", "total_amount"},
            )
        prompt = mock_chat.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        assert "orders" in full
        assert "total_amount" in full

    @pytest.mark.asyncio
    async def test_prompt_contains_allowed_columns_constraint(self):
        """prompt 必须声明只能用白名单列 (防幻觉)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT id FROM orders")) as mock_chat:
            await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id", "total_amount", "user_id"},
            )
        prompt = mock_chat.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        # 白名单列名应出现在约束说明里
        assert "total_amount" in full or "user_id" in full

    @pytest.mark.asyncio
    async def test_prompt_contains_datatype_constraint(self):
        """prompt 必须含 data_type 约束 (防 SUM on VARCHAR, 对标 RAG-005)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT id FROM orders")) as mock_chat:
            await generate_sql(
                question="总销售额",
                schema_context="orders(total_amount DECIMAL, status VARCHAR)",
                allowed_columns={"id", "total_amount", "status"},
            )
        prompt = mock_chat.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        assert "DECIMAL" in full or "VARCHAR" in full

    @pytest.mark.asyncio
    async def test_prompt_contains_fewshot(self):
        """fewshot 示例注入 prompt (对标 RAG-004)。"""
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("SELECT id FROM orders")) as mock_chat:
            await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id"},
                fewshot_examples="参考 SQL: SELECT id FROM orders WHERE status='paid'",
            )
        prompt = mock_chat.call_args.kwargs.get("messages", [])
        full = json.dumps(prompt, ensure_ascii=False)
        assert "status='paid'" in full or "参考" in full


# ── 失败降级 ──────────────────────────────────────────────────

class TestDegradation:
    """LLM 失败 → error (不抛, T025 决定终止/自愈)。"""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id"},
            )
        assert result.error is not None
        assert result.sql == ""

    @pytest.mark.asyncio
    async def test_empty_llm_response_returns_error(self):
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=_mock_llm_content("")):
            result = await generate_sql(
                question="订单",
                schema_context="orders(id)",
                allowed_columns={"id"},
            )
        assert result.error is not None
        assert result.sql == ""
