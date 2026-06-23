"""
T025: Agent 主循环 — LangGraph StateGraph 状态机测试

对标:
  - Claude Code §2 query.ts while(true): 循环不是单次调用
  - AEE-006 严格状态机: intent→schema→generate→execute→self_check→visualize→final
  - 硬规则: 仅按序前进; 上游失败→final(failed); GENERAL/EXPLANATION→直接final

测试策略:
  - mock 各节点 (intent/retriever/sql_agent/executor/checker/chart)
  - 测状态机流转 + 失败路由 + 各意图分支
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.agent import AgentState, run_agent, AgentStage


def _mock_intent(intent: str, confidence: float = 0.9, question: str = "x"):
    from app.ai.intent import IntentOutput
    return IntentOutput(intent=intent, normalized_question=question, confidence=confidence, reason="")


# ── 状态流转: 正常 TEXT_TO_SQL 全流程 ─────────────────────────

class TestFullPipeline:
    """TEXT_TO_SQL: intent→schema→generate→execute→check→visualize→final。"""

    @pytest.mark.asyncio
    async def test_full_success_pipeline(self):
        """完整成功流程, 最终 final(success)。"""
        state = AgentState(question="本月销售额")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("TEXT_TO_SQL", 0.9, "本月销售额"))
        deps.retrieve = AsyncMock(return_value=MagicMock(models=[{"name": "biz_orders", "score": 0.8}], no_match_reason=None))
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error=None, sql="SELECT SUM(total_amount) FROM biz_orders",
            validation=MagicMock(ok=True),
        ))
        deps.execute_sql = AsyncMock(return_value=MagicMock(
            error=None, rows=[(1000,)], columns=["total"], truncated=False,
        ))
        deps.check_result = MagicMock(return_value=MagicMock(ok=True))
        deps.generate_chart = AsyncMock(return_value=MagicMock(ok=True, option={"series": []}))
        deps.should_ask_for_schema = MagicMock(return_value=None)
        deps.should_ask_for_result = MagicMock(return_value=None)

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        assert result.success
        assert result.error is None

    @pytest.mark.asyncio
    async def test_general_intent_short_circuits(self):
        """GENERAL 意图 → 直接 final (不碰 DB, AEE-006)。"""
        state = AgentState(question="你好")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("GENERAL", 0.95, "你好"))

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        deps.retrieve.assert_not_called()  # 没检索
        deps.generate_sql.assert_not_called()


# ── 失败路由: 上游失败 → final(failed) ─────────────────────────

class TestFailureRouting:
    """AEE-006 硬规则: 上游失败直接 final(failed), 不继续。"""

    @pytest.mark.asyncio
    async def test_schema_no_match_routes_to_failed(self):
        """schema 检索无召回且无法确定 → final(failed), 不生成 SQL。"""
        state = AgentState(question="随机问题")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("TEXT_TO_SQL"))
        deps.retrieve = AsyncMock(return_value=MagicMock(models=[], no_match_reason="无法匹配"))
        deps.should_ask_for_schema = MagicMock(return_value=MagicMock(reason="SCHEMA_AMBIGUOUS"))

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        assert not result.success
        deps.generate_sql.assert_not_called()  # 没生成 SQL

    @pytest.mark.asyncio
    async def test_sql_validation_fail_routes_to_failed(self):
        """SQL 校验失败 (生成即危险) → final(failed)。"""
        state = AgentState(question="销售额")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("TEXT_TO_SQL"))
        deps.retrieve = AsyncMock(return_value=MagicMock(models=[{"name": "t", "score": 0.8}], no_match_reason=None))
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error="校验失败", sql="DROP TABLE",
            validation=MagicMock(ok=False),
        ))
        deps.should_ask_for_schema = MagicMock(return_value=None)

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        assert not result.success
        deps.execute_sql.assert_not_called()  # 没执行

    @pytest.mark.asyncio
    async def test_execute_failure_triggers_self_heal(self):
        """执行失败 → 尝试自愈 (对标 while-true 循环)。"""
        state = AgentState(question="销售额")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("TEXT_TO_SQL"))
        deps.retrieve = AsyncMock(return_value=MagicMock(models=[{"name": "t", "score": 0.8}], no_match_reason=None))
        deps.think = AsyncMock(return_value=MagicMock(error=None))
        deps.generate_sql = AsyncMock(return_value=MagicMock(
            error=None, sql="SELECT bad FROM t", validation=MagicMock(ok=True),
        ))
        # 第一次执行失败, 自愈后第二次执行成功 (side_effect 依次返回)
        fail_exec = MagicMock(error="Unknown column 'bad'", rows=[], columns=[])
        ok_exec = MagicMock(error=None, rows=[(1,)], columns=["id"])
        deps.execute_sql = AsyncMock(side_effect=[fail_exec, ok_exec])
        deps.heal_sql = AsyncMock(return_value=MagicMock(
            success=True, sql="SELECT id FROM t", validation=MagicMock(ok=True),
        ))
        deps.should_ask_for_schema = MagicMock(return_value=None)
        deps.check_result = MagicMock(return_value=MagicMock(ok=True))
        deps.generate_chart = AsyncMock(return_value=MagicMock(ok=True))
        deps.should_ask_for_result = MagicMock(return_value=None)
        deps.max_self_heal_rounds = 2  # int, 不是 MagicMock

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        deps.heal_sql.assert_called_once()  # 触发了自愈


# ── 状态机约束: 前向 only ─────────────────────────────────────

class TestStateMachine:
    def test_stage_order(self):
        """阶段必须按序 (AEE-006)。"""
        stages = list(AgentStage)
        assert stages.index(AgentStage.INTENT) < stages.index(AgentStage.SCHEMA_SEARCH)
        assert stages.index(AgentStage.SCHEMA_SEARCH) < stages.index(AgentStage.GENERATE_SQL)
        assert stages.index(AgentStage.GENERATE_SQL) < stages.index(AgentStage.EXECUTE_SQL)
        assert stages.index(AgentStage.EXECUTE_SQL) < stages.index(AgentStage.SELF_CHECK)
        assert stages.index(AgentStage.SELF_CHECK) < stages.index(AgentStage.VISUALIZE)
        assert stages.index(AgentStage.VISUALIZE) < stages.index(AgentStage.FINAL)

    @pytest.mark.asyncio
    async def test_explanation_intent_skips_sql(self):
        """EXPLANATION → 解释后直接 final (不走 SQL 管道)。"""
        state = AgentState(question="这个SQL什么意思")
        deps = MagicMock()
        deps.classify_intent = AsyncMock(return_value=_mock_intent("EXPLANATION", 0.85))

        result = await run_agent(state, deps)

        assert result.stage == AgentStage.FINAL
        deps.retrieve.assert_not_called()


# ── 轮数上限 (对标 Claude Code query.ts 循环保护) ─────────────

class TestLoopGuard:
    """agent_max_llm_calls_per_query 上限 (防无限循环)。"""

    @pytest.mark.asyncio
    async def test_state_initializes_counters(self):
        """AgentState 初始化计数器。"""
        state = AgentState(question="x")
        assert state.llm_call_count == 0
        assert state.self_heal_rounds == 0
