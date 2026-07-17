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

from app.ai.chat_utils import build_schema_context_fallback

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
    # 自然语言回复 (GENERAL/EXPLANATION 意图, 或将来结果摘要的统一出口)
    reply: str = ""
    # 语义层内容 (白名单列 + schema context 的权威来源, 对标 RAG-005)
    semantic_content: Any = None  # SemanticModelContent
    # 计数器 (对标 query.ts 循环保护)
    llm_call_count: int = 0
    self_heal_rounds: int = 0
    # ask_user 请求 (暂停时填充)
    ask_user_request: Any = None
    # 是否持久化到对话历史 (纯闲聊 GENERAL 不入历史)
    persist: bool = True
    # 多轮对话历史文本 (对标 ARC-04: 追问时注入 intent/think/generate_sql)
    # 由调用方 (chat.py/chat_stream.py) 从 StateStore 读取 + format_history_text 生成
    history: str | None = None
    # 命中的 few-shot 示例数 (由 run_agent 从 GenerateResult 传播, RAG-004 可观测)
    fewshot_count: int = 0
    # 上一轮的 SQL (对标 ARC-04: CHART_MODIFY 时复用上轮 SQL 不重新生成)
    # 由调用方 (chat.py/chat_stream.py) 从 StateStore.prev_state 填充
    prev_sql: str = ""
    # 上一轮涉及的表 (追问表继承: 检索后合并上轮表, 保证追问不丢 schema)
    # 由调用方从 StateStore.prev_state.current_tables 填充
    prev_tables: list[str] = field(default_factory=list)
    # 本轮最终使用的表名列表 (含检索命中 + 继承 + 关系扩展, 持久化到 StateStore)
    # 由 run_agent 在 schema 扩展后填充, 持久化时从此字段取值
    current_tables: list[str] = field(default_factory=list)
    # 图谱驱动: 扩展前的种子表 (供前端展示图谱扩展过程)
    seed_tables: list[str] = field(default_factory=list)
    # 图谱驱动: 扩展新增的表 (seed_tables → current_tables 的差集)
    expanded_tables: list[str] = field(default_factory=list)
    # 图谱驱动: 预计算的 JOIN 路径文本 (供前端展示)
    join_path_section: str = ""
    # 降级标记 (对标 O8: 检索/图表降级时前端可提示用户结果可能不精确)
    degraded: bool = False
    # 错误分类: True = 内部异常 (不发给客户端), False = 业务错误 (可发)
    # 由赋值 state.error 的地方决定; 客户端只看非 internal 的错误
    error_is_internal: bool = False
    # 自愈前的原始 SQL (首次自愈时记录, 供持久化/调试对比用)
    heal_before_sql: str | None = None


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
    generate_reply: Any = None
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
        state.intent_output = await deps.classify_intent(state.question, history=state.history)
        state.llm_call_count += 1

        intent = state.intent_output.intent

        # GENERAL/EXPLANATION → 生成自然语言回复, 不走 SQL 管道 (AEE-006)
        if intent in ("GENERAL", "EXPLANATION"):
            if deps.generate_reply is not None:
                state.reply = await deps.generate_reply(state.question, intent)
                state.llm_call_count += 1
            state.persist = False  # 纯闲聊不入对话历史
            state.stage = AgentStage.FINAL
            state.success = True
            return state

        # CHART_MODIFY → 只改图表类型不改 SQL (对标 ARC-04: 复用上轮 SQL)
        # Phase 5 多轮上下文已就绪: 从 prev_sql 取上轮 SQL → 执行 → 用新 chart_type_hint 出图
        if intent == "CHART_MODIFY":
            if not state.prev_sql:
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = "图表修改需要对话上下文, 请先查询数据后再修改图表类型"
                state.reply = '请先查询你想看的数据 (例如「各品类销量」), 然后再让我换图表类型。'
                return state
            # SEC (对标 S7): prev_sql 来自持久化状态, 必须重新校验
            # 即使上轮已校验, 语义层/白名单可能已变更, 且持久化数据可能被篡改
            from app.core.sql_validator import validate_sql
            revalidation = validate_sql(state.prev_sql, allowed_columns=set())
            if not revalidation.ok:
                logger.warning("CHART_MODIFY: prev_sql 校验失败: %s", revalidation.reason)
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = "上轮 SQL 已不再合规, 请重新提问"
                state.reply = "上轮 SQL 校验未通过, 请重新提问, 我会生成新的查询。"
                state.error_is_internal = False  # 业务错误: 用户可操作
                return state
            # 复用上轮 SQL 执行; 表未变, 继承 prev_tables 到 current_tables
            state.sql = state.prev_sql
            state.current_tables = list(state.prev_tables)
            chart_hint = state.intent_output.chart_type_hint if hasattr(state.intent_output, "chart_type_hint") else None
            exec_result = await deps.execute_sql(state.sql)
            state.execute_result = exec_result
            if exec_result.error:
                state.error = f"上轮 SQL 执行失败: {exec_result.error}"
                state.error_is_internal = True  # DB 异常可能含连接串等内部信息
                state.stage = AgentStage.FINAL
                state.success = False
                return state
            # 用新 chart_type_hint 生成图表
            chart = await deps.generate_chart(
                question=state.intent_output.normalized_question or state.question,
                columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                chart_type_hint=chart_hint,
            )
            state.llm_call_count += 1
            state.chart_option = chart.option if hasattr(chart, "option") else None
            # 对标 O8: CHART_MODIFY 也传播图表降级
            if getattr(chart, "degraded", False):
                state.degraded = True
            state.reply = f"已将图表切换为 {chart_hint or '新'} 类型。"
            state.stage = AgentStage.FINAL
            state.success = True
            return state

        # CLARIFICATION → 需要 ask_user (低置信/追问)
        if intent == "CLARIFICATION":
            from app.ai.ask_user import AskUserRequest, AskUserReason
            state.current_tables = list(state.prev_tables)  # 继承上轮表, 防追问丢表
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
        # NOTE: retrieve 是向量检索 (embedding 相似度), 不是 LLM 调用, 不计 llm_call_count
        state.retrieved_models = retrieval.models
        # 对标 O8: 传播降级标记 (LLM 精筛失败时 degraded=True)
        if retrieval.degraded:
            state.degraded = True

        # schema 不确定 → ask_user (对标 proposal.md:120)
        ask = deps.should_ask_for_schema(retrieval)
        if ask is not None:
            state.current_tables = list(state.prev_tables)  # 继承上轮表, 防追问丢表
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = "Schema 不确定, 需要用户确认"
            state.ask_user_request = ask
            return state

        # 无召回且无 ask (宁缺毋滥, 不 fallback)
        if retrieval.no_match_reason and not state.retrieved_models:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = retrieval.no_match_reason
            return state

        # ── Stage 3: 预思考 ───────────────────────────────────
        # schema context + 白名单列从语义层取 (权威来源, 不靠检索文本正则猜)
        from app.ai.schema_utils import build_schema_context, expand_with_relationships, extract_allowed_columns, build_join_path_section, get_schema_graph
        from app.ai.chat_utils import inherit_prev_tables
        from app.ai.intent import safe_normalized_question
        question = safe_normalized_question(state.intent_output, state.question)
        retrieved_names = [m.get("name", "") for m in state.retrieved_models if m.get("name")]
        # 追问表继承: 检索结果 ∪ 上轮表 (追问时上轮表必然相关, 补齐检索可能遗漏的表)
        retrieved_names = inherit_prev_tables(state.prev_tables, retrieved_names, state.semantic_content)
        logger.info("Stage3 预思考: 检索命中表 %s", retrieved_names)
        # 请求级 SchemaGraph 单例: 只构建一次, 共享给 expand + join_path
        sg = get_schema_graph(state.semantic_content)
        # 记录图谱扩展前的种子表 (供前端展示扩展过程)
        seed_tables = list(retrieved_names)
        # 沿关系图谱扩展关联表 (对标 V1 两阶段: 选表→关联扩展→生成)
        # 如选中 biz_products, 沿外键补入 biz_order_items, 否则 JOIN 查询缺表
        retrieved_names = expand_with_relationships(state.semantic_content, retrieved_names, graph=sg)
        # 记录本轮最终使用的表 (含继承 + 关系扩展), 供持久化到 StateStore
        state.current_tables = list(retrieved_names)
        state.seed_tables = seed_tables
        state.expanded_tables = sorted(set(retrieved_names) - set(seed_tables))
        schema_context = build_schema_context(state.semantic_content, retrieved_names)
        if not schema_context:
            # 语义层为空时退化用检索文本 (兜底)
            schema_context = build_schema_context_fallback(state.retrieved_models)
        state.schema_context = schema_context
        # 图驱动的 JOIN 路径 (预计算 ON 条件, 减少 LLM 推理负担)
        # 只对种子表+1-hop 邻居算路径, 避免社区远亲产生大量无意义路径对
        join_path_section = build_join_path_section(state.semantic_content, retrieved_names, graph=sg, seed_names=seed_tables)
        state.join_path_section = join_path_section
        state.thinking = await deps.think(question, schema_context, state.retrieved_models, history=state.history)
        state.llm_call_count += 1

        # ── Stage 4+5: SQL 生成/校验/执行/自愈 统一循环 (对标 while-true) ──
        # 设计: 生成→校验→执行, 任一步失败→自愈(改SQL)→重新校验+执行
        # (对标 Claude Code §2: 工具是循环延续; 不因单次失败终止)
        state.stage = AgentStage.GENERATE_SQL
        # 白名单列: 取整个语义层的全部列 (语义层本身是安全边界, 所有列都允许查询)
        # retrieved_names 只影响 schema_context (给 LLM 的提示), 不限制白名单
        # 否则 JOIN 一个未命中的关联表时, 其列不在白名单 → 误拒正确 SQL
        allowed_columns = extract_allowed_columns(state.semantic_content)

        # 防御: 无语义层 → allowed_columns 空 → Layer3 白名单失效 (安全降级)
        # 对标 fail-closed: 没有列约束信息时拒绝生成 SQL, 而非放行
        if not allowed_columns:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = "无语义层定义, 无法做白名单约束, 拒绝生成 SQL (请先扫描数据源)"
            return state

        # 生成首版 SQL (注入预思考提示, 对标 REF-001 "think before generate")
        from app.ai.thinking import format_thinking_hint
        thinking_hint = format_thinking_hint(state.thinking)
        gen_result = await deps.generate_sql(
            question=question,
            schema_context=schema_context,
            allowed_columns=allowed_columns,
            history=state.history,
            thinking_hint=thinking_hint,
            join_path_section=join_path_section,
        )
        state.llm_call_count += 1

        # 生成彻底失败 (LLM 挂了) → final(failed)
        if gen_result.error and not gen_result.sql:
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = gen_result.error
            state.error_is_internal = True  # LLM API 错误可能含模型名/端点
            return state

        state.sql = gen_result.sql
        # 将 fewshot 命中数从 GenerateResult 传播到 AgentState (RAG-004 可观测)
        state.fewshot_count = gen_result.fewshot_count
        last_error = None  # 校验或执行的错误 (喂给自愈)

        # 校验首版 SQL
        if not gen_result.validation.ok:
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
            # 记录自愈前的原始 SQL (仅首次, 供持久化/调试对比)
            if state.heal_before_sql is None:
                state.heal_before_sql = state.sql
            logger.info("SQL 自愈第 %d 轮 (错误: %s)", state.self_heal_rounds, str(last_error)[:80])

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
                state.error_is_internal = True  # 自愈错误可能含 schema 上下文等内部信息
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
            state.error_is_internal = True  # DB 错误可能含连接串
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

        # 结果异常 → 先尝试自动修正 (带 suggestion), 仍异常才 ask_user (AEE-003)
        # 对标 max_self_heal_rounds 守卫: 自检修正也消耗自愈配额, 避免无限循环
        if not check.ok and check.suggestion and state.self_heal_rounds < deps.max_self_heal_rounds:
            # 用自检建议作纠正方向, 重新生成+执行 SQL (对标 AEE-003 自动修正回路)
            state.self_heal_rounds += 1  # 先占配额, 与 SQL 自愈循环一致
            logger.info("结果自检异常 (%s), 尝试自动修正 (第 %d 轮): %s", check.issue, state.self_heal_rounds, check.suggestion)
            heal_result = await deps.heal_sql(
                sql=state.sql, error=check.reason,
                allowed_columns=allowed_columns, schema_context=schema_context,
            )
            state.llm_call_count += 1
            if heal_result.success:
                state.sql = heal_result.sql
                # 重新执行修正后的 SQL
                exec_result = await deps.execute_sql(state.sql)
                state.execute_result = exec_result
                if not exec_result.error:
                    # 复检: 修正后是否正常
                    recheck = deps.check_result(
                        rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                        columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                        sql=state.sql,
                    )
                    state.check_result = recheck
                    if recheck.ok:
                        logger.info("结果自检自动修正成功")
                        check = recheck  # 修正成功, 跳过 ask_user

        # 仍异常 (自动修正失败或无 suggestion) → ask_user
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
        # 对标 O8: 图表降级也传播 (LLM 失败 → 规则推断时 degraded=True)
        if getattr(chart, "degraded", False):
            state.degraded = True

        # ── Stage 8: final(success) ──────────────────────────
        state.stage = AgentStage.FINAL
        state.success = True
        # ARC-02: prev_sql_review 是 LLM 内部反思 (注入下一轮 Prompt 辅助追问优化)
        # 不再拼入 reply 暴露给用户 — 技术性建议干扰阅读, 且历史回放时无意义
        return state

    except Exception as e:
        # 兜底: 未预期异常 → final(failed) (fail-closed)
        logger.exception("Agent 执行异常")
        state.stage = AgentStage.FINAL
        state.success = False
        state.error = f"Agent 执行异常: {e}"
        state.error_is_internal = True
        return state
