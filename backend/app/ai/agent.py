"""
T025: Agent 主循环 — LangGraph StateGraph 编排 (对标 Claude Code query.ts while-true)

对标:
  - Claude Code §2 query.ts: while(true) 循环, 工具是循环延续, 上游失败 break
  - AEE-006 严格状态机: intent→schema→generate→execute→self_check→visualize→final
  - AEE-007: execute_sql 最多 1 次
  - AEE-002: self_heal 最多 2 轮
  - §2.3 关键设计点: 每轮检查上限, 失败语义清晰

状态机 (AEE-006):
  intent → schema_search → generate_sql → execute_sql → self_check → visualize → final
  硬规则: 仅按序前进; 上游失败→final(failed); GENERAL/EXPLANATION→直接final

循环点 (对标 query.ts while-true):
  execute_sql 失败 → heal_sql → 重新 execute (最多 max_rounds 轮)
  不是线性, 是有条件的回环 (self-heal loop)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AgentStage(str, Enum):
    """状态机阶段 (对标 AEE-006, 严格前向)。"""
    INTENT = "intent"
    SCHEMA_SEARCH = "schema_search"
    GENERATE_SQL = "generate_sql"
    EXECUTE_SQL = "execute_sql"
    SELF_CHECK = "self_check"
    VISUALIZE = "visualize"
    FINAL = "final"


@dataclass
class AgentState:
    """Agent 执行状态 (对标 AEE-005 State Store)。

    每轮持久化到 Checkpointer, 追问时恢复 (维度继承)。
    """
    question: str
    # 阶段流转
    stage: AgentStage = AgentStage.INTENT
    success: bool = False
    error: str | None = None
    # 中间产物
    intent_output: Any = None
    retrieved_models: list[dict] = field(default_factory=list)
    schema_context: str = ""
    thinking: Any = None
    sql: str = ""
    execute_result: Any = None
    check_result: Any = None
    chart_option: dict | None = None
    # 计数器 (对标 query.ts 循环保护)
    llm_call_count: int = 0
    self_heal_rounds: int = 0
    # ask_user 请求 (暂停时填充)
    ask_user_request: Any = None


@dataclass
class AgentDeps:
    """Agent 依赖注入 (各节点服务, 测试时可 mock)。

    对标 Claude Code ToolUseContext — 依赖通过注入而非全局, 便于测试/替换。
    """
    classify_intent: Any = None
    retrieve: Any = None
    think: Any = None
    generate_sql: Any = None
    execute_sql: Any = None
    heal_sql: Any = None
    check_result: Any = None
    generate_chart: Any = None
    should_ask_for_schema: Any = None
    should_ask_for_result: Any = None
    # 配置 (从 settings 读, 测试可覆盖)
    max_self_heal_rounds: int = 2


async def run_agent(state: AgentState, deps: AgentDeps) -> AgentState:
    """运行 Agent 主循环 (状态机编排, 对标 query.ts while-true)。

    状态机流转 (AEE-006 严格前向):
      intent → schema → generate → execute → [self_heal loop] → check → visualize → final

    硬规则:
      - 上游失败 → 直接 final(failed), 不继续 (§2.3 break 语义)
      - GENERAL/EXPLANATION → 直接 final (不走 SQL 管道)
      - self_heal 最多 max_rounds 轮 (AEE-002)
      - execute_sql 最多 1 次 (AEE-007, 自愈后重执行不算新查询)
    """
    try:
        # ── Stage 1: 意图识别 ─────────────────────────────────
        state.stage = AgentStage.INTENT
        state.intent_output = await deps.classify_intent(state.question)
        state.llm_call_count += 1

        intent = state.intent_output.intent

        # GENERAL/EXPLANATION → 直接 final (AEE-006)
        if intent in ("GENERAL", "EXPLANATION"):
            state.stage = AgentStage.FINAL
            state.success = True
            return state

        # CLARIFICATION → 需要 ask_user (低置信/追问)
        if intent == "CLARIFICATION":
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = f"需要澄清: {state.intent_output.reason}"
            state.ask_user_request = deps.should_ask_for_schema(
                MagicMock(models=[], no_match_reason=state.intent_output.reason)
            ) or MagicMock(reason="CLARIFICATION", question=state.intent_output.reason)
            return state

        # ── Stage 2: schema 检索 ───────────────────────────────
        state.stage = AgentStage.SCHEMA_SEARCH
        question = state.intent_output.normalized_question or state.question
        retrieval = await deps.retrieve(question)
        state.llm_call_count += 1
        state.retrieved_models = retrieval.models if hasattr(retrieval, "models") else []

        # schema 不确定 → ask_user (对标 proposal.md:120)
        ask = deps.should_ask_for_schema(retrieval)
        if ask is not None:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = "Schema 不确定, 需要用户确认"
            state.ask_user_request = ask
            return state

        # 无召回且无 ask (宁缺毋滥, 不 fallback)
        if hasattr(retrieval, "no_match_reason") and retrieval.no_match_reason and not state.retrieved_models:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = retrieval.no_match_reason
            return state

        # ── Stage 3: 预思考 ───────────────────────────────────
        schema_context = _build_schema_context(state.retrieved_models)
        state.schema_context = schema_context
        state.thinking = await deps.think(question, schema_context, state.retrieved_models)
        state.llm_call_count += 1

        # ── Stage 4: SQL 生成 + 校验 ──────────────────────────
        state.stage = AgentStage.GENERATE_SQL
        allowed_columns = _extract_allowed_columns(state.retrieved_models)
        gen_result = await deps.generate_sql(
            question=question,
            schema_context=schema_context,
            allowed_columns=allowed_columns,
            llm_client=None,  # 实际由 deps 内部注入
        )
        state.llm_call_count += 1

        # 生成/校验失败 → final(failed) (上游失败 break)
        if gen_result.error or not (hasattr(gen_result, "validation") and gen_result.validation.ok):
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = gen_result.error or "SQL 校验失败"
            return state

        state.sql = gen_result.sql

        # ── Stage 5: 执行 + 自愈循环 (对标 while-true) ────────
        state.stage = AgentStage.EXECUTE_SQL
        exec_result = await deps.execute_sql(state.sql)
        state.execute_result = exec_result

        # 执行失败 → 自愈循环 (最多 max_rounds 轮)
        while exec_result.error is not None and state.self_heal_rounds < deps.max_self_heal_rounds:
            state.self_heal_rounds += 1
            logger.info("SQL 执行失败, 自愈第 %d 轮", state.self_heal_rounds)

            heal_result = await deps.heal_sql(
                sql=state.sql,
                error=exec_result.error,
                allowed_columns=allowed_columns,
                schema_context=schema_context,
            )
            state.llm_call_count += 1

            if not heal_result.success:
                # 自愈失败 → final(failed)
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = f"SQL 自愈失败 ({state.self_heal_rounds} 轮): {heal_result.error}"
                return state

            # 自愈成功 → 重新执行
            state.sql = heal_result.sql
            exec_result = await deps.execute_sql(state.sql)
            state.execute_result = exec_result

        # 执行仍有错 (自愈耗尽) → final(failed)
        if exec_result.error is not None:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = f"SQL 执行失败 (自愈 {state.self_heal_rounds} 轮未解决): {exec_result.error}"
            return state

        # ── Stage 6: 结果自检 ────────────────────────────────
        state.stage = AgentStage.SELF_CHECK
        check = await deps.check_result(
            rows=exec_result.rows if hasattr(exec_result, "rows") else [],
            columns=exec_result.columns if hasattr(exec_result, "columns") else [],
            sql=state.sql,
        )
        state.check_result = check

        # 结果异常 + ask_user 触发 → 暂停问用户
        if not check.ok:
            ask_result = deps.should_ask_for_result(check)
            if ask_result is not None:
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = f"结果异常: {check.reason}"
                state.ask_user_request = ask_result
                return state

        # ── Stage 7: 图表生成 ────────────────────────────────
        state.stage = AgentStage.VISUALIZE
        chart_hint = state.intent_output.chart_type_hint if hasattr(state.intent_output, "chart_type_hint") else None
        chart = await deps.generate_chart(
            question=question,
            columns=exec_result.columns if hasattr(exec_result, "columns") else [],
            rows=exec_result.rows if hasattr(exec_result, "rows") else [],
            chart_type_hint=chart_hint,
        )
        state.llm_call_count += 1
        state.chart_option = chart.option if hasattr(chart, "option") else None

        # ── Stage 8: final(success) ──────────────────────────
        state.stage = AgentStage.FINAL
        state.success = True
        return state

    except Exception as e:
        # 兜底: 未预期异常 → final(failed) (fail-closed)
        logger.exception("Agent 执行异常")
        state.stage = AgentStage.FINAL
        state.success = False
        state.error = f"Agent 执行异常: {e}"
        return state


def _build_schema_context(models: list[dict]) -> str:
    """从检索结果构建 schema_context (供 SQL 生成 prompt)。"""
    if not models:
        return ""
    lines = []
    for m in models:
        name = m.get("name", "")
        text = m.get("text", "")
        lines.append(f"{name}: {text}")
    return "\n".join(lines)


def _extract_allowed_columns(models: list[dict]) -> set[str]:
    """从检索结果提取白名单列 (简化: 实际应从语义层完整列定义取)。

    TODO: 完整实现应从 SemanticModelContent 取全部列名,
    这里先从检索结果的 text 提取占位 (Phase 4 后续完善)。
    """
    cols = set()
    import re
    for m in models:
        text = m.get("text", "")
        # text 格式 "表名 描述 列名(中文名)[类型] ..."
        for match in re.finditer(r"(\w+)\(", text):
            cols.add(match.group(1))
    return cols
