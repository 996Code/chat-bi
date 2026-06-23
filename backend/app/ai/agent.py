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
    # 语义层内容 (白名单列 + schema context 的权威来源, 对标 RAG-005)
    semantic_content: Any = None  # SemanticModelContent
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
            from app.ai.ask_user import AskUserRequest, AskUserReason
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = f"需要澄清: {state.intent_output.reason}"
            state.ask_user_request = AskUserRequest(
                reason=AskUserReason.SCHEMA_AMBIGUOUS,
                question=state.intent_output.reason or "请提供更具体的问题",
            )
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
        # schema context + 白名单列从语义层取 (权威来源, 不靠检索文本正则猜)
        from app.ai.schema_utils import build_schema_context, extract_allowed_columns
        retrieved_names = [m.get("name", "") for m in state.retrieved_models if m.get("name")]
        schema_context = build_schema_context(state.semantic_content, retrieved_names)
        if not schema_context:
            # 语义层为空时退化用检索文本 (兜底)
            schema_context = _build_schema_context_fallback(state.retrieved_models)
        state.schema_context = schema_context
        state.thinking = await deps.think(question, schema_context, state.retrieved_models)
        state.llm_call_count += 1

        # ── Stage 4+5: SQL 生成/校验/执行/自愈 统一循环 (对标 while-true) ──
        # 设计: 生成→校验→执行, 任一步失败→自愈(改SQL)→重新校验+执行
        # (对标 Claude Code §2: 工具是循环延续; 不因单次失败终止)
        state.stage = AgentStage.GENERATE_SQL
        allowed_columns = extract_allowed_columns(state.semantic_content, retrieved_names)

        # 生成首版 SQL
        gen_result = await deps.generate_sql(
            question=question,
            schema_context=schema_context,
            allowed_columns=allowed_columns,
            llm_client=None,  # 实际由 deps 内部注入
        )
        state.llm_call_count += 1

        # 生成彻底失败 (LLM 挂了) → final(failed)
        if gen_result.error and not gen_result.sql:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = gen_result.error
            return state

        state.sql = gen_result.sql
        last_error = None  # 校验或执行的错误 (喂给自愈)

        # 校验首版 SQL
        if hasattr(gen_result, "validation") and not gen_result.validation.ok:
            last_error = f"校验失败 ({gen_result.validation.violated_layer}): {gen_result.validation.reason}"

        # 执行首版 SQL (校验通过才执行)
        state.stage = AgentStage.EXECUTE_SQL
        exec_result = None
        if last_error is None:
            exec_result = await deps.execute_sql(state.sql)
            state.execute_result = exec_result
            if exec_result.error:
                last_error = exec_result.error

        # ── 自愈循环: 校验失败/执行失败 → heal → 重新校验+执行 ──
        while last_error is not None and state.self_heal_rounds < deps.max_self_heal_rounds:
            state.self_heal_rounds += 1
            logger.info("SQL 自愈第 %d 轮 (错误: %s)", state.self_heal_rounds, last_error[:80])

            heal_result = await deps.heal_sql(
                sql=state.sql,
                error=last_error,
                allowed_columns=allowed_columns,
                schema_context=schema_context,
            )
            state.llm_call_count += 1

            if not heal_result.success:
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = f"SQL 自愈失败 ({state.self_heal_rounds} 轮): {heal_result.error}"
                return state

            # 自愈成功 → 更新 SQL, 重新校验 + 执行
            state.sql = heal_result.sql
            last_error = None

            # 重新校验 (自愈后的 SQL 也走三层校验, v1 教训 #32)
            if hasattr(heal_result, "validation") and not heal_result.validation.ok:
                last_error = f"校验失败 ({heal_result.validation.violated_layer}): {heal_result.validation.reason}"
                continue

            # 重新执行
            exec_result = await deps.execute_sql(state.sql)
            state.execute_result = exec_result
            if exec_result.error:
                last_error = exec_result.error

        # 仍有错 (自愈耗尽) → final(failed)
        if last_error is not None:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = f"SQL 失败 (自愈 {state.self_heal_rounds} 轮未解决): {last_error}"
            return state

        # ── Stage 6: 结果自检 ────────────────────────────────
        state.stage = AgentStage.SELF_CHECK
        # check_result 是纯规则同步函数 (不调 LLM), 直接调不用 await
        check = deps.check_result(
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


def _build_schema_context_fallback(models: list[dict]) -> str:
    """兜底: 语义层为空时, 从检索结果 text 构建 schema_context。

    正常路径用 schema_utils.build_schema_context (从语义层完整定义),
    这个仅当 semantic_content 缺失时兜底 (不靠正则猜列名)。
    """
    if not models:
        return ""
    lines = []
    for m in models:
        name = m.get("name", "")
        text = m.get("text", "")
        lines.append(f"{name}: {text}")
    return "\n".join(lines)
