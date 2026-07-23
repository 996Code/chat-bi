"""
Chat Stream API — SSE 流式问答 (对标 V1 pipeline_executor "手撕管线")

设计 (对标 V1, 对标 Claude Code query.ts):
  - 不调用 run_agent() 黑盒, 而是分步调用 deps, 每步后 yield 一个 SSE 事件
  - 前端 fetch + ReadableStream 逐行解析, 实时渲染管线进度 (V1 "细"的来源)
  - 保留 POST /chat 非流式端点不变 (向后兼容, 测试走它)

SSE 事件 (对标 V1, 适配 V2 状态机):
  intent  — 意图识别 (含 intent; GENERAL 时含 reply)
  schema  — Schema 检索 (含命中的表)
  sql     — SQL 生成 (含 sql/validation)
  data    — 执行结果 (含 columns/rows)
  heal    — 自愈 (含 retry/error_code)
  chart   — 图表生成
  persist_warning — 反哺失败警告 (stage/error/conversation_id/question)
  complete — 结束 (含 success/conversation_id)
  error   — 异常

格式: "event: <type>\\ndata: <json>\\n\\n" (标准 SSE)
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentDeps, AgentState, AgentStage
from app.ai.chat_utils import normalize_value, serialize_thinking, build_schema_context_fallback
from app.api.chat import ChatRequest
from app.core.auth import AuthUser, require_user, write_audit_log
from app.db.models import DataSource
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# 限流 (复用 chat.py 同款配置, 防流式端点绕过限流)
from app.core.rate_limit import get_limiter as _get_limiter
_limiter = _get_limiter()


def _chat_rate():
    """查询限流值 (从 config 读, 对标 rate_limit_queries_per_minute)。"""
    from app.core.config import get_settings
    return f"{get_settings().rate_limit_queries_per_minute}/minute"


def _build_thinking_with_graph(state) -> dict:
    """将 ThinkingResult 序列化并合并图谱扩展信息 (供 thinking 字段持久化)。"""
    _thinking = serialize_thinking(state.thinking) or {}
    _thinking["seed_tables"] = state.seed_tables
    _thinking["expanded_tables"] = state.expanded_tables
    _thinking["join_path_section"] = state.join_path_section
    return _thinking


def _sse(event: str, data: dict, seq: int | None = None) -> str:
    """格式化一条 SSE 事件 (标准格式 + event ID)。

    M2: 加 event ID 字段, 支持断线重连 (Last-Event-ID)。
    seq=None 时不输出 id 行 (兼容旧调用)。
    """
    parts = []
    if seq is not None:
        parts.append(f"id: {seq}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(parts) + "\n\n"


def _format_token_usage(token_stats: dict | None) -> dict | None:
    """格式化 token_usage: 与 REST 端点 TokenUsagePayload 结构一致 (对标一致性)。

    SSE 不需要 Pydantic 验证, 但确保字段名/结构与 ChatResponse.token_usage 对齐。
    extra=allow 保证 nodes 等额外字段透传。
    """
    if not token_stats:
        return None
    return {
        "prompt_tokens": token_stats.get("prompt_tokens", 0),
        "completion_tokens": token_stats.get("completion_tokens", 0),
        "total_tokens": token_stats.get("total_tokens", 0),
        "llm_calls": token_stats.get("llm_calls", 0),
        # nodes 等额外字段透传
        **{k: v for k, v in token_stats.items()
           if k not in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_calls")},
    }


@_limiter.limit(_chat_rate)
@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE 流式问答 — 分步 yield 管线事件 (对标 V1 pipeline_executor)。"""
    question = body.question.strip()
    data_source_id = body.data_source_id
    conversation_id = body.conversation_id

    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    # 确定数据源 (未指定 → 取第一个)
    if not data_source_id:
        ds = (
            await db.execute(
                select(DataSource).where(
                    DataSource.tenant_filter(user.tenant_id),
                    DataSource.is_active == True,  # noqa: E712
                ).limit(1)
            )
        ).scalar_one_or_none()
        if ds is None:
            raise HTTPException(status_code=404, detail="未找到可用数据源, 请先添加")
        data_source_id = ds.id

    # 装配依赖 (复用 chat.py 的 build_agent_deps)
    from app.api.chat import build_agent_deps
    try:
        deps, content = await build_agent_deps(data_source_id, user.tenant_id, db)
    except Exception as e:
        logger.exception("Agent 依赖装配失败")
        raise HTTPException(status_code=500, detail="Agent 初始化失败, 请稍后重试")

    # 追问时恢复上下文 (对标 ARC-04): 取历史轮次 → 格式化 (含压缩+状态补偿)
    history_text = None
    prev_sql = ""
    prev_state = None
    if conversation_id:
        try:
            from app.ai.state_store import StateStore, format_history_text
            store = StateStore()
            prev_state = store.load(user.tenant_id, conversation_id)
            prev_sql = prev_state.current_sql if prev_state else ""
            turns = store.list_turns(user.tenant_id, conversation_id)
            if turns:
                prev_tables = (prev_state.current_tables if prev_state else None)
                history_text = await format_history_text(
                    turns, semantic_tables=prev_tables,
                )
        except Exception as e:
            logger.warning("流式历史恢复失败, 当新对话处理: %s", e)

    async def event_stream():
        """分步执行 Agent 管线, 每步 yield SSE 事件 (对标 V1 手撕管线)。

        持久化在 finally 统一执行一次 (单一出口, 对标 V1 的"审计在管线末尾",
        而非每个分支各自写 — 避免遗漏/重复持久化导致一次对话多条记录)。
        """
        state = AgentState(question=question, semantic_content=content)
        state.history = history_text  # 注入多轮上下文 (intent/think/generate_sql 都用)
        state.prev_sql = prev_sql  # CHART_MODIFY 复用上轮 SQL
        state.prev_tables = prev_state.current_tables if prev_state else []  # 追问表继承
        conv_id = conversation_id or str(uuid.uuid4()).replace("-", "")[:32]
        # M2: SSE event ID 自增序号 (断线重连用)
        _seq = 0
        # 追踪自愈前的原始 SQL (持久化用, 取第一次自愈前的 SQL)
        # AgentState.heal_before_sql 字段追踪, 与 REST 管线一致

        def emit(event: str, data: dict, node: str | None = None) -> str:
            """格式化 SSE 事件并自增序号。

            Args:
                node: AI 节点名, 传入后自动附带该节点的 LLM 调用信息
                      (call_count + total_tokens), 前端在 pipeline 步骤上展示。
            """
            nonlocal _seq
            _seq += 1
            if node:
                nu = get_node_usage(node)
                if nu:
                    data["node_usage"] = nu
            return _sse(event, data, _seq)
        # OBS-002: 启动 token 追踪 (请求级)
        # T050: 启动 prompt 捕获 (请求级, dump-prompts 导出用)
        from app.core.token_tracker import start_token_tracking, stop_token_tracking, get_node_usage
        from app.core.prompt_capture import start_prompt_capture, stop_prompt_capture
        _tt_token = start_token_tracking()
        _pc_token = start_prompt_capture()
        # 各步骤耗时收集 (历史对话回放用)
        _step_durations: dict[str, int] = {}
        try:
            # ── Stage 0: 首事件 — 立刻发 conversation_id ──────────
            # 本地 LLM 慢, 完整 pipeline 要 1-2 分钟; 让前端尽早拿到 conv_id
            # (侧边栏可提前显示对话项 + header 灰色展示 id 方便定位)
            # 同步落骨架行: 把用户问题先存下, 即使中途刷新/断流, 历史里也能看到"问过这个问题"
            # (末尾 _persist 会复用骨架 turn 号追加完整行, 不会产生重复轮次)
            _skeleton_turn = await _persist_skeleton(user, question, conv_id, is_new=not conversation_id)
            yield emit("start", {"conversation_id": conv_id})

            # ── Stage 1: 意图识别 ───────────────────────────────
            t0 = time.monotonic()
            state.intent_output = await deps.classify_intent(state.question, history=state.history)
            state.llm_call_count += 1
            intent = state.intent_output.intent

            # GENERAL/EXPLANATION → 生成回复后结束 (纯闲聊, 不持久化为对话记录)
            if intent in ("GENERAL", "EXPLANATION"):
                if deps.generate_reply is not None:
                    state.reply = await deps.generate_reply(state.question, intent)
                    state.llm_call_count += 1
                state.success = True
                state.stage = AgentStage.FINAL
                # 闲聊也入库 (用户要求: 历史记录要完整留存, 含闲聊)
                yield emit("intent", {
                    "intent": intent,
                    "reply": state.reply,
                    "duration_ms": round((time.monotonic() - t0) * 1000),
                }, node="intent")
                return

            yield emit("intent", {
                "intent": intent,
                "duration_ms": round((time.monotonic() - t0) * 1000),
            }, node="intent")
            _step_durations["intent"] = round((time.monotonic() - t0) * 1000)

            # CHART_MODIFY → 复用上轮 SQL, 只换图表类型 (对标 ARC-04)
            if intent == "CHART_MODIFY":
                if not state.prev_sql:
                    state.reply = '图表修改需要对话上下文, 请先查询数据后再换图表类型。'
                    state.error = state.reply
                    state.stage = AgentStage.FINAL
                    state.success = False
                    yield emit("intent", {"intent": intent, "reply": state.reply}, node="intent")
                    return
                # SEC (对标 S7): prev_sql 来自持久化状态, 必须重新校验
                from app.core.sql_validator import validate_sql
                revalidation = validate_sql(state.prev_sql, allowed_columns=set())
                if not revalidation.ok:
                    state.error = "上轮 SQL 已不再合规, 请重新提问"
                    state.reply = "上轮 SQL 校验未通过, 请重新提问, 我会生成新的查询。"
                    state.stage = AgentStage.FINAL
                    state.success = False
                    state.error_is_internal = False  # 业务错误: 用户可操作
                    yield emit("error", {"error": state.error})
                    return
                state.sql = state.prev_sql
                state.current_tables = list(state.prev_tables)  # 表未变, 继承上轮
                chart_hint = state.intent_output.chart_type_hint if hasattr(state.intent_output, "chart_type_hint") else None
                # 执行上轮 SQL 取数据
                exec_result = await deps.execute_sql(state.sql)
                state.execute_result = exec_result
                if exec_result.error:
                    state.error = f"上轮 SQL 执行失败: {exec_result.error}"
                    state.error_is_internal = True  # DB 异常可能含连接串
                    state.stage = AgentStage.FINAL
                    state.success = False
                    yield emit("error", {"error": "SQL 执行失败"})
                    return
                # 数据事件 (前端展示)
                yield emit("data", {
                    "columns": list(exec_result.columns) if hasattr(exec_result, "columns") else [],
                    "rows": [[normalize_value(v) for v in r] for r in exec_result.rows] if hasattr(exec_result, "rows") else [],
                    "row_count": len(exec_result.rows) if hasattr(exec_result, "rows") else 0,
                    "truncated": exec_result.truncated if hasattr(exec_result, "truncated") else False,
                })
                # 用新 chart_type_hint 生成图表
                t0 = time.monotonic()
                chart = await deps.generate_chart(
                    question=state.intent_output.normalized_question or state.question,
                    columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                    rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                    chart_type_hint=chart_hint,
                )
                state.llm_call_count += 1
                state.chart_option = chart.option if hasattr(chart, "option") else None
                # 对标 O8: 图表降级也传播
                if getattr(chart, "degraded", False):
                    state.degraded = True
                if state.chart_option is not None:
                    yield emit("chart", {
                        "option": state.chart_option,
                        "duration_ms": round((time.monotonic() - t0) * 1000),
                    }, node="generate_chart")
                state.reply = f"已将图表切换为 {chart_hint or '新'} 类型。"
                state.stage = AgentStage.FINAL
                state.success = True
                return

            # CLARIFICATION → ask_user
            if intent == "CLARIFICATION":
                from app.ai.ask_user import AskUserRequest, AskUserReason
                state.current_tables = list(state.prev_tables)  # 继承上轮表, 防追问丢表
                state.stage = AgentStage.FINAL
                state.success = False
                state.ask_user_request = AskUserRequest(
                    reason=AskUserReason.SCHEMA_AMBIGUOUS,
                    question=state.intent_output.reason or "请提供更具体的问题",
                )
                state.error = f"需要澄清: {state.intent_output.reason}"
                yield emit("clarify", {
                    "question": state.ask_user_request.question,
                    "reason": str(state.ask_user_request.reason.value)
                        if hasattr(state.ask_user_request.reason, "value")
                        else str(state.ask_user_request.reason),
                    "options": getattr(state.ask_user_request, "options", None),
                })
                return

            # ── Stage 2: schema 检索 ─────────────────────────────
            t0 = time.monotonic()
            from app.ai.intent import safe_normalized_question
            norm_q = safe_normalized_question(state.intent_output, question)
            retrieval = await deps.retrieve(norm_q)
            # NOTE: retrieve 是向量检索, 不是 LLM 调用, 不计 llm_call_count
            state.retrieved_models = retrieval.models
            # 对标 O8: 传播降级标记
            if retrieval.degraded:
                state.degraded = True
            # 只取 type=model 的记录作为表名; type=metric 的命中由 build_metrics_hint 处理
            tables = [
                m.get("name", "") for m in state.retrieved_models
                if m.get("name") and m.get("type") != "metric"
            ]
            # 指标命中 → 反查所属表 (用户问"GMV"可能只召回 metric 记录, 需补入所属表)
            # 同时收集检索命中的指标 (供前端 pipeline 展示)
            metric_hits_retrieval = []
            if state.semantic_content:
                metric_names = set()
                for m in state.retrieved_models:
                    if m.get("type") == "metric" and m.get("name"):
                        metric_names.add(m["name"])
                if metric_names:
                    for model in state.semantic_content.models:
                        for metric in model.metrics:
                            if metric.name in metric_names:
                                metric_hits_retrieval.append({
                                    "name": metric.name,
                                    "display_name": metric.display_name,
                                    "table": model.name,
                                    "source": metric.source,
                                    "type": metric.type,
                                })
                                if model.name not in tables:
                                    tables.append(model.name)
            yield emit("schema", {
                "tables": tables,
                "metric_hits": metric_hits_retrieval,
                "duration_ms": round((time.monotonic() - t0) * 1000),
            }, node="retrieve")
            _step_durations["schema"] = round((time.monotonic() - t0) * 1000)

            # schema 不确定 / 无召回 → ask_user 或结束
            ask = deps.should_ask_for_schema(retrieval)
            if ask is not None:
                state.current_tables = list(state.prev_tables)  # 继承上轮表, 防追问丢表
                state.stage = AgentStage.FINAL
                state.success = False
                state.ask_user_request = ask
                state.error = "Schema 不确定, 需要用户确认"
                _reason = getattr(ask, "reason", "")
                yield emit("clarify", {
                    "question": getattr(ask, "question", "请确认要查询的表"),
                    "reason": str(_reason.value) if hasattr(_reason, "value") else str(_reason),
                    "options": getattr(ask, "options", None),
                })
                return
            if retrieval.no_match_reason and not state.retrieved_models:
                state.current_tables = list(state.prev_tables)  # 继承上轮表, 防追问丢表
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = retrieval.no_match_reason
                state.error_is_internal = False  # 业务错误: 无匹配表
                yield emit("error", {"error": "服务内部错误, 请稍后重试" if state.error_is_internal else state.error})
                return

            # ── Stage 3: 预思考 + schema context ────────────────
            from app.ai.schema_utils import build_schema_context, expand_with_relationships, extract_allowed_columns, build_join_path_section, get_schema_graph, build_metrics_hint
            from app.ai.chat_utils import inherit_prev_tables
            # 追问表继承: 检索结果 ∪ 上轮表 (追问时上轮表必然相关, 补齐检索可能遗漏的表)
            tables = inherit_prev_tables(state.prev_tables, tables, state.semantic_content)
            logger.info("Stage3 预思考: 检索命中表 %s", tables)
            # 请求级 SchemaGraph 单例: 只构建一次, 共享给 expand + join_path
            sg = get_schema_graph(state.semantic_content)
            # 记录图谱扩展前的种子表 (供前端展示扩展过程)
            seed_tables = list(tables)
            # 沿关系图谱扩展关联表 (对标 V1 两阶段: 选表→关联扩展→生成)
            tables = expand_with_relationships(state.semantic_content, tables, graph=sg)
            # 记录本轮最终使用的表 (含继承 + 关系扩展), 供持久化到 StateStore
            state.current_tables = list(tables)
            state.seed_tables = seed_tables
            state.expanded_tables = sorted(set(tables) - set(seed_tables))
            schema_context = build_schema_context(state.semantic_content, tables)
            if not schema_context:
                schema_context = build_schema_context_fallback(state.retrieved_models)
            state.schema_context = schema_context
            # 图驱动的 JOIN 路径 (预计算 ON 条件, 减少 LLM 推理负担)
            # 只对种子表+1-hop 邻居算路径, 避免社区远亲产生大量无意义路径对
            join_path_section = build_join_path_section(state.semantic_content, tables, graph=sg, seed_names=seed_tables)
            state.join_path_section = join_path_section
            # 业务指标定义 (从语义层指标区提取, 供 LLM 准确计算指标而非猜测公式)
            metrics_hint = build_metrics_hint(state.semantic_content, tables)
            # 白名单列: 取整个语义层的全部列 (语义层本身是安全边界)
            allowed_columns = extract_allowed_columns(state.semantic_content)
            if not allowed_columns:
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = "无语义层定义, 请先扫描数据源"
                state.error_is_internal = False  # 业务错误: 未扫描数据源
                yield emit("error", {"error": "服务内部错误, 请稍后重试" if state.error_is_internal else state.error})
                return
            t_think = time.monotonic()
            state.thinking = await deps.think(norm_q, schema_context, state.retrieved_models, history=state.history)
            state.llm_call_count += 1
            # 预思考 SSE 事件 (REF-001: 推送选表理由+聚合+陷阱, 前端可展开查看)
            thinking = state.thinking
            if thinking and not getattr(thinking, "error", None):
                # 图谱扩展: 种子表 vs 扩展后表
                expanded_tables = sorted(set(tables) - set(seed_tables))
                yield emit("thinking", {
                    "tables": getattr(thinking, "tables", []),
                    "aggregation": getattr(thinking, "aggregation", ""),
                    "caveats": getattr(thinking, "caveats", []),
                    "prev_sql_review": getattr(thinking, "prev_sql_review", ""),
                    "duration_ms": round((time.monotonic() - t_think) * 1000),
                    # 图谱驱动: 扩展过程 + JOIN 路径
                    "seed_tables": seed_tables,
                    "expanded_tables": expanded_tables,
                    "join_path_section": join_path_section,
                }, node="thinking")
                _step_durations["thinking"] = round((time.monotonic() - t_think) * 1000)

            # ── Stage 4+5: SQL 生成/校验/执行 + 自愈循环 ──────────
            t0 = time.monotonic()
            from app.ai.thinking import format_thinking_hint
            thinking_hint = format_thinking_hint(state.thinking)
            gen_result = await deps.generate_sql(
                question=norm_q, schema_context=schema_context,
                allowed_columns=allowed_columns, history=state.history,
                thinking_hint=thinking_hint,
                join_path_section=join_path_section,
                metrics_hint=metrics_hint,
            )
            state.llm_call_count += 1

            if gen_result.error and not gen_result.sql:
                state.stage = AgentStage.FINAL
                state.success = False
                state.error = gen_result.error
                state.error_is_internal = True  # LLM API 错误可能含模型名/端点
                yield emit("sql", {"error": "SQL 生成失败", "duration_ms": round((time.monotonic() - t0) * 1000)}, node="generate_sql")
                return

            state.sql = gen_result.sql
            state.fewshot_count = gen_result.fewshot_count  # 传播到 AgentState (与 agent.py 一致)

            # 分析 SQL 引用了哪些语义层指标 (供前端 pipeline 展示)
            sql_metric_hits = []
            if state.semantic_content and state.sql:
                import re as _re
                _agg_re = _re.compile(
                    r"\b(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*([^)]+)\s*\)", _re.IGNORECASE,
                )
                sql_aggs = [
                    (m.group(1).upper(), m.group(2).strip())
                    for m in _agg_re.finditer(state.sql)
                ]
                if sql_aggs:
                    for model in state.semantic_content.models:
                        for metric in model.metrics:
                            if metric.type == "single" and metric.formula:
                                formula_upper = metric.formula.upper()
                                for func, col in sql_aggs:
                                    agg_expr = f"{func}({col})".upper()
                                    if agg_expr in formula_upper or f"{func} ( {col} )" in formula_upper:
                                        sql_metric_hits.append({
                                            "name": metric.name,
                                            "display_name": metric.display_name,
                                            "table": model.name,
                                            "source": metric.source,
                                            "type": metric.type,
                                        })
                                        break

            yield emit("sql", {
                "sql": state.sql,
                "fewshot_count": gen_result.fewshot_count,
                "metric_hits": sql_metric_hits,
                "duration_ms": round((time.monotonic() - t0) * 1000),
            }, node="generate_sql")
            _step_durations["generate_sql"] = round((time.monotonic() - t0) * 1000)

            validation_ok = True
            violated = ""
            violated_layer = ""
            if not gen_result.validation.ok:
                validation_ok = False
                violated = gen_result.validation.reason
                violated_layer = gen_result.validation.violated_layer

            last_error = None if validation_ok else f"校验失败 ({violated_layer}): {violated}"
            exec_result = None
            t_exec = time.monotonic()  # SQL 执行计时 (含自愈后重执行)

            # 首次执行
            if last_error is None:
                exec_result = await deps.execute_sql(state.sql)
                state.execute_result = exec_result
                if exec_result.error:
                    last_error = exec_result.error

            # 自愈循环
            max_rounds = deps.max_self_heal_rounds
            while last_error is not None and state.self_heal_rounds < max_rounds:
                state.self_heal_rounds += 1
                t0 = time.monotonic()
                heal_result = await deps.heal_sql(
                    sql=state.sql, error=last_error,
                    allowed_columns=allowed_columns, schema_context=schema_context,
                )
                state.llm_call_count += 1
                # 记录自愈前的 SQL (OBS-003: 展示修复前后对比)
                before_sql = state.sql
                if state.heal_before_sql is None:
                    state.heal_before_sql = before_sql
                if not heal_result.success:
                    yield emit("heal", {
                        "retry": state.self_heal_rounds, "success": False,
                        "error": "自愈失败", "before_sql": before_sql,
                    }, node="heal_sql")
                    state.error = f"SQL 自愈失败 ({state.self_heal_rounds} 轮): {heal_result.error}"
                    state.error_is_internal = True  # 自愈错误可能含 schema 上下文
                    break
                state.sql = heal_result.sql
                yield emit("heal", {
                    "retry": state.self_heal_rounds, "success": True,
                    "sql": state.sql, "before_sql": before_sql,
                    "error": "已修复",
                    "duration_ms": round((time.monotonic() - t0) * 1000),
                }, node="heal_sql")
                # 重新校验 + 执行
                heal_valid = True
                if hasattr(heal_result, "validation") and not heal_result.validation.ok:
                    heal_valid = False
                    last_error = f"校验失败 ({heal_result.validation.violated_layer}): {heal_result.validation.reason}"
                if heal_valid:
                    exec_result = await deps.execute_sql(state.sql)
                    state.execute_result = exec_result
                    last_error = exec_result.error if exec_result.error else None

            # 执行结果事件
            if exec_result and not exec_result.error:
                _exec_dur = round((time.monotonic() - t_exec) * 1000)
                yield emit("data", {
                    "columns": list(exec_result.columns) if hasattr(exec_result, "columns") else [],
                    "rows": [[normalize_value(v) for v in r] for r in exec_result.rows] if hasattr(exec_result, "rows") else [],
                    "row_count": len(exec_result.rows) if hasattr(exec_result, "rows") else 0,
                    "truncated": exec_result.truncated if hasattr(exec_result, "truncated") else False,
                    "duration_ms": _exec_dur,
                })
                _step_durations["execute_sql"] = _exec_dur
            elif last_error:
                state.stage = AgentStage.FINAL
                state.success = False
                if not state.error:
                    state.error = f"SQL 失败 (自愈 {state.self_heal_rounds} 轮未解决): {last_error}"
                    state.error_is_internal = True  # DB 错误可能含连接串
                return

            # ── Stage 6: 结果自检 ────────────────────────────────
            if exec_result:
                check = deps.check_result(
                    rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                    columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                    sql=state.sql,
                )
                state.check_result = check

                # 结果异常 → 自动修正尝试 (AEE-003, 与 run_agent 对齐)
                # 对标 max_self_heal_rounds 守卫: 自检修正也消耗自愈配额, 避免无限循环
                if not check.ok and check.suggestion and state.self_heal_rounds < max_rounds:
                    logger.info("流式结果自检异常 (%s), 尝试自动修正", check.issue)
                    heal_result = await deps.heal_sql(
                        sql=state.sql, error=check.reason,
                        allowed_columns=allowed_columns, schema_context=schema_context,
                    )
                    state.llm_call_count += 1
                    if heal_result.success:
                        state.sql = heal_result.sql
                        state.self_heal_rounds += 1
                        exec_result = await deps.execute_sql(state.sql)
                        state.execute_result = exec_result
                        if not exec_result.error:
                            recheck = deps.check_result(
                                rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                                columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                                sql=state.sql,
                            )
                            state.check_result = recheck
                            if recheck.ok:
                                check = recheck
                            else:
                                # 重发修正后的数据
                                yield emit("data", {
                                    "columns": list(exec_result.columns) if hasattr(exec_result, "columns") else [],
                                    "rows": [[normalize_value(v) for v in r] for r in exec_result.rows] if hasattr(exec_result, "rows") else [],
                                    "row_count": len(exec_result.rows) if hasattr(exec_result, "rows") else 0,
                                    "truncated": exec_result.truncated if hasattr(exec_result, "truncated") else False,
                                })

                # 仍异常 → ask_user (流式入口补全, 对标 ARC-03)
                if not check.ok:
                    ask_result = deps.should_ask_for_result(check)
                    if ask_result is not None:
                        state.stage = AgentStage.FINAL
                        state.success = False
                        state.ask_user_request = ask_result
                        state.error = f"结果异常: {check.reason}"
                        _reason = getattr(ask_result, "reason", "")
                        yield emit("clarify", {
                            "question": getattr(ask_result, "question", "结果可能异常"),
                            "reason": str(_reason.value) if hasattr(_reason, "value") else str(_reason),
                            "options": getattr(ask_result, "options", None),
                        })
                        return

            # ── Stage 7: 图表 ────────────────────────────────────
            t0 = time.monotonic()
            chart_hint = state.intent_output.chart_type_hint if hasattr(state.intent_output, "chart_type_hint") else None
            chart = await deps.generate_chart(
                question=norm_q,
                columns=exec_result.columns if hasattr(exec_result, "columns") else [],
                rows=exec_result.rows if hasattr(exec_result, "rows") else [],
                chart_type_hint=chart_hint,
            )
            state.llm_call_count += 1
            state.chart_option = chart.option if hasattr(chart, "option") else None
            # 对标 O8: 图表降级也传播 (LLM 失败 → 规则推断时 degraded=True)
            if getattr(chart, "degraded", False):
                state.degraded = True
            if state.chart_option is not None:
                _chart_dur = round((time.monotonic() - t0) * 1000)
                yield emit("chart", {
                    "option": state.chart_option,
                    "duration_ms": _chart_dur,
                }, node="generate_chart")
                _step_durations["generate_chart"] = _chart_dur

            # ── Stage 8: 完成 ────────────────────────────────────
            state.stage = AgentStage.FINAL
            state.success = True
            # ARC-02: prev_sql_review 是 LLM 内部反思 (注入下一轮 Prompt 辅助追问优化)
            # 不再拼入 reply 暴露给用户 — 技术性建议干扰阅读, 且历史回放时无意义

        except Exception as e:
            logger.exception("流式 Agent 异常")
            state.stage = AgentStage.FINAL
            state.success = False
            state.error = f"Agent 执行异常: {e}"
            state.error_is_internal = True
            yield emit("error", {"error": "服务内部错误, 请稍后重试"})
        except BaseException as be:  # asyncio.CancelledError / GeneratorExit (客户端断开)
            logger.warning("流式被中断: %s (stage=%s sql_len=%d)", type(be).__name__, state.stage, len(state.sql or ""))
            raise
        finally:
            # 统一持久化 + complete 事件 (单一出口, 对标 V1: 审计在管线末尾一次性)
            # 闲聊也入库 (历史记录完整留存); persist 字段保留作兜底守卫
            should_persist = getattr(state, "persist", True)
            # T050: 取出 prompt 记录 (DEBUG 模式才持久化, prompt 可能含敏感 schema)
            from app.core.config import get_settings
            prompt_capture = stop_prompt_capture(_pc_token)
            # OBS-002: 结束 token 追踪 (在 _persist 前, token_stats 需要存入 ConversationState)
            token_stats = stop_token_tracking(_tt_token)
            if should_persist:
                if get_settings().debug and prompt_capture:
                    # 通过 state 属性传给 _persist (避免改 _persist 签名)
                    state._prompt_records = prompt_capture.get("records", [])
                # 传递 token_stats (避免改 _persist 签名, 通过 state 属性)
                state._token_stats = token_stats
                # 传递各步骤耗时 (历史对话回放用)
                state._step_durations = _step_durations
                try:
                    persist_warnings = await _persist(db, user, state, conv_id, conversation_id, data_source_id, deps)
                    # E1 Task 2.3: 反哺失败通过 persist_warning SSE 事件告知前端 (Fail-Closed, 不静默)
                    for warn in persist_warnings:
                        yield emit("persist_warning", {
                            "stage": warn["stage"],
                            "error": warn["error"],
                            "conversation_id": conv_id,
                            "question": state.question,
                        })
                except Exception as pe:
                    logger.error("finally _persist 抛异常: %s", pe, exc_info=True)
            # 客户端安全: 内部异常不泄露详情, 业务错误保留原文
            _client_error = None
            if not state.success and state.error:
                _client_error = "服务内部错误, 请稍后重试" if state.error_is_internal else state.error
            yield emit("complete", {
                "success": state.success,
                "conversation_id": conv_id if should_persist else None,
                "error": _client_error,
                "token_usage": _format_token_usage(token_stats),
                "prompts_count": len(prompt_capture.get("records", [])) if prompt_capture else 0,
                "degraded": state.degraded,  # 对标 O8: 前端可提示用户结果可能不精确
                # 与 REST ChatResponse 对齐 (前端 SSE 消费者也需要这些字段)
                "stage": state.stage.value if hasattr(state.stage, "value") else str(state.stage),
                "llm_calls": state.llm_call_count,
                "self_heal_rounds": state.self_heal_rounds,
                # 指标命中: 告知前端本次查询命中了哪些业务指标
                "metric_hits": getattr(state, "_metric_hits", None) or [],
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _persist_skeleton(user, question: str, conv_id: str, is_new: bool) -> int | None:
    """落骨架行 — start 时先把用户问题存下, 防止中途刷新/断流丢失提问记录。

    仅落问题 (sql/结果留空), pipeline 末尾 _persist 追加完整行并复用骨架的 turn 号
    (见 _persist 的 turn 推算)。即使 pipeline 中途被 CancelledError 中断, 用户至少
    能在历史里看到"问过这个问题"。

    Returns: 骨架行的 turn 号 (供 _persist 复用), None 表示未落 (如追问已有历史无需骨架)。
    """
    try:
        from app.ai.state_store import ConversationState, StateStore
        from app.core.config import get_settings
        store = StateStore()
        existing = store.list_turns(user.tenant_id, conv_id)
        # 追问场景 (非新对话) 且已有轮次 → 不落骨架 (当前轮末尾 _persist 会正常 append)
        if not is_new and existing:
            return None
        turn = max((t.get("turn", 0) for t in existing), default=0) + 1
        _max_title = get_settings().conversation_title_max_length
        conv_state = ConversationState(
            title=question[:_max_title].strip() or "新对话",
            first_question=question if is_new else "",
            question=question,
            # 标记骨架行: 末尾 _persist 识别后复用 turn 号 (避免重复轮次)
            result_summary={"skeleton": True},
        )
        store.save(user.tenant_id, conv_id, turn, conv_state)
        logger.info("骨架行已落库: conv=%s turn=%d q=%r", conv_id, turn, question[:30])
        return turn
    except Exception as e:
        logger.warning("骨架行落库失败 (不阻塞): %s", e)
        return None


async def _persist(db, user, state, conv_id, req_conv_id, data_source_id, deps) -> list[dict]:
    """持久化对话状态 + 标题 + 审计 (流式版, 复用 chat.py 同款逻辑)。
    
    Returns:
        list[dict]: 反哺失败警告列表, 每项包含 {stage, error}
    """
    persist_warnings: list[dict] = []
    
    # StateStore 持久化
    try:
        from app.ai.state_store import ConversationState, StateStore
        from app.core.config import get_settings
        store = StateStore()
        prev_state = store.load(user.tenant_id, conv_id) if req_conv_id else None
        is_new = prev_state is None
        title = ""
        _max_title = get_settings().conversation_title_max_length
        if is_new:
            from app.core.llm_client import llm_chat
            try:
                title, _ = await llm_chat(
                    messages=[
                        {"role": "system", "content": f"把用户的提问总结为一个简短的对话标题(不超过{_max_title}个字, 不要标点)。只输出标题文字。"},
                        {"role": "user", "content": state.question},
                    ],
                    temperature=0.0,
                )
                title = title.strip().strip('"\'""')[:_max_title] or state.question[:_max_title].strip() or "新对话"
            except Exception as e:
                logger.warning("标题生成失败, 降级为问题截断: %s", e)
                title = state.question[:_max_title].strip() or "新对话"
        exec_result = state.execute_result
        # 结果采样 (防大结果撑爆 JSONL, 采样行数可配置)
        rows_sample = []
        cols = []
        if exec_result and hasattr(exec_result, "rows"):
            _row_limit = get_settings().state_store_row_sample_limit
            rows_sample = [
                [normalize_value(v) for v in r] for r in (exec_result.rows or [])[:_row_limit]
            ]
            cols = list(exec_result.columns) if hasattr(exec_result, "columns") else []

        # 指标反哺: 提前执行, 结果写入 state._metric_hits 供 ConversationState 构造使用
        if state.success and state.sql:
            try:
                from app.ai.recall import persist_metric_feedback
                from app.core.agent_memory import AgentMemoryStore
                mem_dir = f"memory/{user.tenant_id}/{data_source_id}"
                mem_store = AgentMemoryStore(base_dir=mem_dir)
                co_updates = persist_metric_feedback(mem_store, state, state.semantic_content, conv_id=conv_id)
                # 持久化 co_occurrence 增量 (不走版本化, 直接原地更新 content JSON)
                if co_updates:
                    from app.api.chat import _persist_co_occurrence
                    await _persist_co_occurrence(db, user.tenant_id, data_source_id, co_updates)
                # 传递指标命中信息 (供 SSE complete + 审计日志 + ConversationState)
                # co_occurrence 用 delta 模式, 前端展示用 delta 标记命中
                state._metric_hits = [
                    {
                        "table": u["table_name"],
                        "metric": u["metric_name"],
                        "co_occurrence": u.get("delta", 1),
                        "source": u.get("source"),
                        "type": u.get("type"),
                    }
                    for u in co_updates
                ]
            except Exception as e:
                logger.debug("指标反哺失败 (不阻塞): %s", e)

        conv_state = ConversationState(
            current_tables=state.current_tables,
            current_sql=state.sql or "",
            result_summary={
                "row_count": len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
                "success": state.success,
            } if (exec_result or state.success) else {},
            chart_type=state.chart_option.get("chart_type") or (state.chart_option.get("series", [{}])[0].get("type") if state.chart_option.get("series") else None) if state.chart_option else None,
            title=title,
            first_question=state.question if is_new else "",
            # 本轮完整信息 (历史对话恢复用)
            question=state.question,
            reply=state.reply or "",
            columns=cols,
            rows_sample=rows_sample,
            chart_option=state.chart_option,
            # 预思考 (历史对话恢复展示) + 图谱扩展信息
            thinking=_build_thinking_with_graph(state),
            # T050: prompt 记录 (DEBUG 模式, dump-prompts 导出用)
            prompts=getattr(state, "_prompt_records", None),
            # 增强字段: 意图 / token / 耗时 / 错误 / 自愈
            intent=state.intent_output.intent if state.intent_output else None,
            token_usage=getattr(state, "_token_stats", None),
            sql_duration_ms=getattr(exec_result, "duration_ms", None) if exec_result else None,
            step_durations=getattr(state, "_step_durations", None) or None,
            error=state.error or None,
            self_heal_rounds=state.self_heal_rounds,
            heal_before_sql=state.heal_before_sql,
            # 全量落库: 主动确认内容 (刷新后还原 Agent 的确认问题 + 候选选项)
            ask_user={
                "question": state.ask_user_request.question,
                "options": getattr(state.ask_user_request, "options", None),
                "reason": str(state.ask_user_request.reason.value)
                    if hasattr(state.ask_user_request.reason, "value")
                    else str(state.ask_user_request.reason),
            } if state.ask_user_request else None,
            # 指标命中 (历史对话恢复展示)
            metric_hits=getattr(state, "_metric_hits", None),
        )
        # turn 推算: 优先复用本轮骨架行的 turn 号 (start 事件落的, 避免重复轮次);
        # 无骨架则取 max(行数, 最大turn值) + 1 (防御自愈历史脏数据)
        existing_turns = store.list_turns(user.tenant_id, conv_id)
        turn = None
        if existing_turns:
            last = existing_turns[-1]
            last_st = last.get("state", {}) or {}
            # 骨架行特征: result_summary.skeleton=True 且 question 匹配本轮
            if last_st.get("result_summary", {}).get("skeleton") and last_st.get("question") == state.question:
                turn = last.get("turn")
                logger.info("复用骨架行 turn 号: conv=%s turn=%d", conv_id, turn)
        if turn is None:
            max_existing_turn = max((t.get("turn", 0) for t in existing_turns), default=0)
            turn = max(len(existing_turns), max_existing_turn) + 1
        store.save(user.tenant_id, conv_id, turn, conv_state)

        # 保存查询记录 (SavedQuery): 成功的 SQL 查询入库, 供看板展示 / fewshot 回流
        # 对标 ARC-05 + Dashboard: 看板页从 saved_queries 拉图表
        # 去重: 同 tenant + question + sql 不重复插入 (防追问/重复提问导致膨胀)
        if state.success and state.sql:
            try:
                from app.db.models import SavedQuery
                dup_check = await db.execute(
                    select(SavedQuery.id).where(
                        SavedQuery.tenant_id == user.tenant_id,
                        SavedQuery.data_source_id == data_source_id,
                        SavedQuery.question == state.question,
                        SavedQuery.sql_text == state.sql,
                    ).limit(1)
                )
                if dup_check.scalar_one_or_none() is None:
                    saved = SavedQuery(
                        tenant_id=user.tenant_id,
                        user_id=user.user_id,
                        data_source_id=data_source_id,
                        conversation_id=conv_id,
                        question=state.question,
                        sql_text=state.sql,
                        result_summary=json.dumps({
                            "row_count": len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
                        }, ensure_ascii=False),
                        chart_config=state.chart_option,
                    )
                    db.add(saved)
            except Exception as e:
                logger.warning("SavedQuery 写入失败 (不阻塞): %s", e)
                persist_warnings.append({"stage": "saved_query", "error": "保存查询记录失败"})

        # Few-shot SQL 回流: 成功查询 → 向量库 (RAG-004, 失败不阻塞)
        if state.success and state.sql:
            try:
                from app.services.fewshot import index_fewshot_example
                from app.services.embedder import get_embedder
                await index_fewshot_example(
                    question=state.question,
                    sql=state.sql,
                    embedder=get_embedder(),
                    data_source_id=data_source_id,
                )
            except Exception:
                logger.debug("fewshot 回流失败 (不阻塞)")
                persist_warnings.append({
                    "stage": "fewshot",
                    "error": "示例回流失败",
                })

        # LLM 自主提炼记忆: 成功查询后判断是否产生值得保留的新知识
        if state.success and state.sql:
            try:
                from app.ai.recall import extract_memory_from_turn
                from app.core.agent_memory import AgentMemoryStore
                mem_dir = f"memory/{user.tenant_id}/{data_source_id}"
                extracted = await extract_memory_from_turn(
                    question=state.question,
                    sql=state.sql,
                    tables=state.current_tables or [],
                    reply=state.reply or "",
                    memory_dir=mem_dir,
                )
                if extracted:
                    mem_store = AgentMemoryStore(base_dir=mem_dir)
                    mem_store.save_memory(
                        name=extracted["name"],
                        description=extracted["description"],
                        content=extracted["content"],
                        memory_type=extracted.get("type", "project"),
                        extra_metadata={"conversation_id": conv_id},
                    )
                    logger.info("LLM 自主提炼记忆: %s (ds=%s)", extracted["name"], data_source_id)
            except Exception:
                logger.debug("LLM 自主提炼记忆失败 (不阻塞)")
                persist_warnings.append({
                    "stage": "memory_extract",
                    "error": "记忆提炼失败",
                })

        # E1: 链路经验沉淀 (成功查询 + 多表)
        if state.success and state.sql and len(state.current_tables) >= 2:
            try:
                from app.ai.recall import persist_linkage_memory
                from app.core.agent_memory import AgentMemoryStore
                mem_dir = f"memory/{user.tenant_id}/{data_source_id}"
                mem_store = AgentMemoryStore(base_dir=mem_dir)
                persist_linkage_memory(mem_store, state, conv_id=conv_id)
            except Exception as e:
                logger.warning("链路经验沉淀失败: %s", e)
                persist_warnings.append({
                    "stage": "linkage",
                    "error": "链路经验沉淀失败",
                })

    except Exception as e:
        logger.warning("流式 StateStore 持久化失败 (不阻塞): %s", e)
        persist_warnings.append({
            "stage": "state_store",
            "error": "状态持久化失败",
        })

    # 审计 (try/except 包裹, 避免审计异常丢失已收集的 persist_warnings)
    try:
        # SEC-006: SQL 注入拦截专项标识 — Layer 1(AST/多语句/写操作) 或 Layer 2(危险函数) 失败
        # 视为注入拦截, 用 action="sql_injection_blocked" 单独标记便于检索
        _INJECTION_LAYERS = ("AST", "dangerous_function")
        is_injection_block = bool(
            state.error and any(f"({layer})" in state.error for layer in _INJECTION_LAYERS)
        )
        # DSO-07: 从执行结果取耗时, 判定慢查询 (阈值可配置)
        from app.core.config import get_settings
        _settings = get_settings()
        exec_duration = getattr(state.execute_result, "duration_ms", None) if state.execute_result else None
        is_slow = bool(
            exec_duration is not None
            and exec_duration >= _settings.sql_slow_query_threshold * 1000
        )
        # 指标命中信息写入审计日志 detail
        _metric_hits = getattr(state, "_metric_hits", None)
        _audit_detail = {"metric_hits": _metric_hits} if _metric_hits else None
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="chat",
            action="sql_injection_blocked" if is_injection_block else "query",
            status="success" if state.success else "fail",
            sql_text=state.sql or None,
            error_message=state.error[:500] if state.error else None,
            duration_ms=exec_duration,
            is_slow=is_slow,
            data_source_id=data_source_id,
            detail=_audit_detail,
        )
        await db.commit()
    except Exception as e:
        logger.warning("审计日志写入失败 (不阻塞): %s", e)
        persist_warnings.append({
            "stage": "audit",
            "error": "审计日志写入失败",
        })
    
    return persist_warnings
