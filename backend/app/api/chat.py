"""
Chat API — 自然语言问答入口 (Agent 执行引擎 HTTP 暴露)

对标:
  - AEE-001: Agent 执行链路 (T025 主循环)
  - FRT-002: 前端交互 (SSE 流式, 本版先做同步返回, SSE 留 Phase 7)

POST /chat { question, data_source_id? } → Agent 全流程结果
  intent → schema 检索 → SQL 生成 → 校验 → 执行 → 自愈 → 自检 → 图表

依赖装配: build_agent_deps() 把真实服务接进 AgentDeps (测试用 mock)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentDeps, AgentState, run_agent
from app.core.auth import AuthUser, require_user, write_audit_log
from app.core.config import get_settings
from app.db.models import DataSource, SemanticModel
from app.db.session import get_db
from app.ai.chat_utils import normalize_value, serialize_thinking
from app.schemas.semantic_layer import SemanticModelContent
from app.services.datasource_engine import datasource_to_url, get_engine_pool

logger = logging.getLogger(__name__)

# 限流器 (慢导入避免循环, main.py 注册到 app.state)
from app.core.rate_limit import get_limiter as _get_limiter
_limiter = _get_limiter()

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """问答请求。"""
    question: str
    data_source_id: str | None = None  # None → 用用户默认/第一个数据源
    conversation_id: str | None = None  # None → 新对话; 有值 → 追问 (恢复上下文)


class AskUserPayload(BaseModel):
    """对标 F4: ask_user 结构化 (替代 untyped dict)。"""
    reason: str
    question: str
    options: list[str] | None = None


class TokenUsagePayload(BaseModel):
    """对标 F4: token_usage 结构化 (替代 untyped dict)。"""
    model_config = {"extra": "allow"}  # 允许 nodes 等额外字段透传, 不丢弃
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0


class ChatResponse(BaseModel):
    """问答结果 (Agent 全流程产出)。"""
    success: bool
    conversation_id: str | None = None  # 追问时带上恢复上下文
    intent: str | None = None
    question: str | None = None  # normalized_question
    sql: str | None = None
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    truncated: bool = False
    chart: dict | None = None
    error: str | None = None
    reply: str | None = None  # 自然语言回复 (GENERAL/EXPLANATION 意图)
    ask_user: AskUserPayload | None = None  # 对标 F4: 结构化替代 untyped dict
    # 过程信息 (调试/可观测)
    stage: str = ""
    llm_calls: int = 0
    self_heal_rounds: int = 0
    token_usage: TokenUsagePayload | None = None  # 对标 F4: 结构化替代 untyped dict
    fewshot_count: int = 0  # 命中的 few-shot 示例数 (RAG-004)
    degraded: bool = False  # 对标 O8: 检索/图表降级标记 (前端可提示用户结果可能不精确)


def _safe_response_question(intent_output, original: str) -> str:
    """对标 D4: 响应中 question 字段防空 (与 safe_normalized_question 一致)。"""
    if intent_output is None:
        return original
    nq = (getattr(intent_output, "normalized_question", None) or "").strip()
    return nq if nq else original


async def build_agent_deps(
    data_source_id: str,
    tenant_id: str,
    db: AsyncSession,
) -> tuple[AgentDeps, SemanticModelContent | None]:
    """装配 Agent 真实依赖 (LLM/retriever/sql_agent/executor 等注入 AgentDeps)。

    返回 (deps, semantic_content) — content 用于白名单列/schema context。
    """
    from app.services.retriever import retrieve as _retrieve
    from app.services.vector_store import get_vector_store
    from app.ai.intent import classify_intent
    from app.ai.thinking import think
    from app.ai.sql_agent import generate_sql
    from app.services.sql_executor import execute_sql
    from app.ai.sql_healer import heal_sql
    from app.ai.result_checker import check_result
    from app.ai.chart_agent import generate_chart
    from app.ai.replier import generate_reply
    from app.ai.ask_user import should_ask_for_schema, should_ask_for_result

    store = get_vector_store()

    # Skills: 加载业务规则注入 prompt (T040 + T060: 按 db_type 自动加载 reference)
    # 多租户隔离: 按 tenant_id 读 skills/{tenant_id}/, 与 API 端点一致
    try:
        from app.services.skills_loader import SkillsLoader
        # T060: 从数据源获取 db_type, 让 format_for_prompt 注入对应方言规则
        ds_for_skill = (
            await db.execute(
                select(DataSource).where(
                    DataSource.tenant_filter(tenant_id),
                    DataSource.id == data_source_id,
                )
            )
        ).scalar_one_or_none()
        db_type = ds_for_skill.db_type if ds_for_skill else None
        skills_loader = SkillsLoader(base_dir=f"skills/{tenant_id}")
        skills_text = skills_loader.format_for_prompt(db_type=db_type)
    except Exception:
        skills_text = ""

    # 取数据源的语义层 content (白名单列/schema context 来源)
    sm = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(tenant_id),
                SemanticModel.data_source_id == data_source_id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    content = SemanticModelContent(**sm.content) if sm else None

    # 数据源连接 (执行 SQL 用)
    ds = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == data_source_id,
                DataSource.tenant_filter(tenant_id),
            )
        )
    ).scalar_one_or_none()
    # DSO-08: 禁用的数据源拒绝查询 (对标 v1 经验教训 #18, 避免 Partial 陷阱)
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not ds.is_active:
        raise HTTPException(status_code=403, detail="数据源已禁用, 无法查询")
    url = datasource_to_url(ds)

    # Few-shot 检索闭包 (对标 RAG-004: 相似审核 SQL 作 few-shot 注入 prompt)
    # 独立 fewshot collection, 标量过滤防跨数据源召回; 失败降级返回空 (宁缺毋滥)
    from app.services.fewshot import find_fewshot_examples, format_fewshot_prompt
    fewshot_store = get_vector_store("fewshot")
    # Relevant Recall: 按需召回 Agent 记忆注入 prompt (对标 Claude §5.4, T037)
    from app.ai.recall import recall_memories, format_memories_for_prompt

    async def _generate_sql_with_fewshot(
        question: str, schema_context: str, allowed_columns: set[str],
        history: str | None = None, **kw,
    ):
        """生成 SQL (含 few-shot 检索 + 记忆召回 + skills 注入)。"""
        # 1. 检索相似审核 SQL (few-shot)
        fewshot_text = ""
        fewshot_count = 0
        try:
            examples = await find_fewshot_examples(question, fewshot_store, _get_embedder(), data_source_id=data_source_id)
            fewshot_text = format_fewshot_prompt(examples)
            fewshot_count = len(examples)
        except Exception as e:
            logger.debug("fewshot 检索失败, 降级无 fewshot: %s", e)
        # 2. 按需召回相关记忆 (Relevant Recall, 关键词相关性, 不调 LLM)
        # 多租户 + 数据源隔离: 按 tenant_id + data_source_id 读记忆目录
        memory_text = ""
        try:
            memories = recall_memories(question, tenant_id=tenant_id, data_source_id=data_source_id)
            memory_text = format_memories_for_prompt(memories)
        except Exception as e:
            logger.debug("记忆召回失败, 降级无记忆: %s", e)
        # 合并 skills + memory 进同一个 skills 参数 (generate_sql 的 skills 槽位)
        combined_skills = "\n\n".join(s for s in [skills_text, memory_text] if s) or None
        thinking_hint = kw.get("thinking_hint")  # 由 run_agent 传入 (预思考提示)
        result = await generate_sql(
            question=question, schema_context=schema_context,
            allowed_columns=allowed_columns,
            skills=combined_skills, history=history, fewshot_examples=fewshot_text or None,
            thinking_hint=thinking_hint,
        )
        result.fewshot_count = fewshot_count
        return result

    deps = AgentDeps(
        classify_intent=lambda q, history=None, **kw: classify_intent(q, history=history),
        retrieve=lambda q: _retrieve(q, store, _get_embedder(), data_source_id=data_source_id),
        think=lambda q, ctx, models, history=None, **kw: think(q, ctx, models, history=history),
        generate_sql=_generate_sql_with_fewshot,
        execute_sql=lambda sql: execute_sql(sql, data_source_id, url, get_engine_pool()),
        heal_sql=lambda sql, error, allowed_columns, schema_context, **kw: heal_sql(
            sql=sql, error=error, allowed_columns=allowed_columns,
            schema_context=schema_context,
        ),
        check_result=lambda rows, columns, sql: check_result(rows, columns, sql),
        generate_chart=lambda question, columns, rows, chart_type_hint=None, **kw: generate_chart(
            question=question, columns=columns, rows=rows,
            chart_type_hint=chart_type_hint,
        ),
        generate_reply=lambda question, intent, **kw: generate_reply(question, intent),
        should_ask_for_schema=should_ask_for_schema,
        should_ask_for_result=should_ask_for_result,
    )
    return deps, content


def _get_embedder():
    """惰性获取 embedder (避免循环 import)。"""
    from app.services.embedder import get_embedder
    return get_embedder()



def _chat_rate():
    """查询限流值 (从 config 读, 对标 rate_limit_queries_per_minute)。"""
    return f"{get_settings().rate_limit_queries_per_minute}/minute"



async def _generate_conversation_title(question: str, deps) -> str:
    """为新对话生成简短标题。

    用 LLM 总结首条问题为标题, 失败降级为问题截断 (fail-closed, 不阻塞主流程)。
    """
    max_len = get_settings().conversation_title_max_length
    fallback = question[:max_len].strip() or "新对话"
    try:
        from app.core.llm_client import llm_chat
        title, _ = await llm_chat(
            messages=[
                {"role": "system", "content": f"把用户的提问总结为一个简短的对话标题(不超过{max_len}个字, 不要标点)。只输出标题文字。"},
                {"role": "user", "content": question},
            ],
            temperature=0.0,
        )
        title = title.strip().strip('"\'""')
        return title[:max_len] if title else fallback
    except Exception as e:
        logger.warning("标题生成失败, 降级为问题截断: %s", e)
        return fallback


@_limiter.limit(_chat_rate)
@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    req: ChatRequest,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """自然语言问答 → Agent 全流程。

    intent → schema 检索 → SQL 生成 → 校验 → 执行 → 自愈 → 自检 → 图表
    """
    # 确定数据源 (未指定 → 取第一个)
    data_source_id = req.data_source_id
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

    # 装配依赖
    try:
        deps, content = await build_agent_deps(data_source_id, user.tenant_id, db)
    except Exception as e:
        logger.exception("Agent 依赖装配失败")
        raise HTTPException(status_code=500, detail="Agent 初始化失败, 请稍后重试")

    # 追问时恢复上下文 (T036 State Store, 对标 ARC-04 多轮对话)
    import uuid
    conversation_id = req.conversation_id or str(uuid.uuid4()).replace("-", "")[:32]
    prev_state = None
    history_text = None
    if req.conversation_id:
        try:
            from app.ai.state_store import StateStore, format_history_text
            store = StateStore()
            prev_state = store.load(user.tenant_id, conversation_id)
            # 取完整历史轮次 → 格式化 (含压缩 + 状态补偿), 喂给 Agent 各 LLM 节点
            turns = store.list_turns(user.tenant_id, conversation_id)
            if turns:
                # 状态补偿用上一轮涉及的表 (追问时"当前在查的表")
                prev_tables = (prev_state.current_tables if prev_state else None)
                history_text = await format_history_text(
                    turns, semantic_tables=prev_tables,
                )
        except Exception as e:
            logger.warning("历史恢复失败, 当新对话处理: %s", e)
            pass  # 恢复失败不阻塞, 当新对话处理

    # 运行 Agent
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")
    state = AgentState(question=question, semantic_content=content)
    state.history = history_text  # 注入多轮上下文 (intent/think/generate_sql 都用)
    state.prev_sql = prev_state.current_sql if prev_state else ""  # CHART_MODIFY 复用上轮 SQL
    state.prev_tables = prev_state.current_tables if prev_state else []  # 追问表继承
    # OBS-002: 启动 token 追踪 (请求级, 各节点 track_usage 累加)
    # T050: 启动 prompt 捕获 (请求级, 各节点 record_prompt 累积, dump-prompts 导出用)
    from app.core.token_tracker import start_token_tracking, stop_token_tracking
    from app.core.prompt_capture import start_prompt_capture, stop_prompt_capture
    _tt_token = start_token_tracking()
    _pc_token = start_prompt_capture()
    state = await run_agent(state, deps)
    token_stats = stop_token_tracking(_tt_token)
    prompt_capture = stop_prompt_capture(_pc_token)

    # 持久化结构化状态到 StateStore (T036, 支持追问恢复)
    # 纯闲聊 (GENERAL) 不入对话历史 (state.persist=False)
    if getattr(state, "persist", True):
        try:
            from app.ai.state_store import ConversationState, StateStore
            exec_result = state.execute_result
            is_new_conv = prev_state is None
            # 新对话: 生成标题 (LLM 总结 ≤16 字, 失败降级为首条问题截断)
            title = ""
            if is_new_conv:
                title = await _generate_conversation_title(question, deps)
            # 结果采样 (前 50 行)
            rows_sample = []
            cols = []
            if exec_result and hasattr(exec_result, "rows"):
                _row_limit = get_settings().state_store_row_sample_limit
                rows_sample = [
                    [normalize_value(v) for v in r] for r in (exec_result.rows or [])[:_row_limit]
                ]
                cols = list(exec_result.columns) if hasattr(exec_result, "columns") else []
            conv_state = ConversationState(
                current_tables=state.current_tables,
                current_sql=state.sql or "",
                current_filters={},
                result_summary={
                    "row_count": len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
                    "success": state.success,
                } if exec_result or state.success else {},
                chart_type=state.chart_option.get("chart_type") or (state.chart_option.get("series", [{}])[0].get("type") if state.chart_option.get("series") else None) if state.chart_option else None,
                title=title,
                first_question=question if is_new_conv else "",
                question=question,
                reply=state.reply or "",
                columns=cols,
                rows_sample=rows_sample,
                chart_option=state.chart_option,
                # 预思考 (历史对话恢复展示)
                thinking=serialize_thinking(state.thinking),
                # T050: prompt 记录 (DEBUG 模式才持久化, dump-prompts 导出用)
                prompts=prompt_capture.get("records", []) if get_settings().debug and prompt_capture else None,
                # 增强字段: 意图 / token / 耗时 / 错误 / 自愈
                intent=state.intent_output.intent if state.intent_output else None,
                token_usage=token_stats if token_stats else None,
                sql_duration_ms=getattr(exec_result, "duration_ms", None) if exec_result else None,
                error=state.error or None,
                self_heal_rounds=state.self_heal_rounds,
                heal_before_sql=state.heal_before_sql,
            )
            # turn_number: 从已有轮次推算 (防御: 取 max(行数, 最大turn值) + 1, 自愈历史脏数据)
            existing_turns = StateStore().list_turns(user.tenant_id, conversation_id)
            max_existing_turn = max((t.get("turn", 0) for t in existing_turns), default=0)
            turn_number = max(len(existing_turns), max_existing_turn) + 1
            StateStore().save(user.tenant_id, conversation_id, turn_number, conv_state)

            # 保存查询记录 (SavedQuery): 成功的 SQL 查询入库, 供 fewshot 回流 / 历史挖掘用
            # 对标 ARC-05: SavedQuery 是历史查询挖掘的输入数据源
            # 去重: 同 tenant + question + sql 不重复插入 (防追问/重复提问导致膨胀)
            if state.success and state.sql:
                try:
                    from app.db.models import SavedQuery
                    import json
                    dup_check = await db.execute(
                        select(SavedQuery.id).where(
                            SavedQuery.tenant_id == user.tenant_id,
                            SavedQuery.data_source_id == data_source_id,
                            SavedQuery.question == question,
                            SavedQuery.sql_text == state.sql,
                        ).limit(1)
                    )
                    if dup_check.scalar_one_or_none() is None:
                        saved = SavedQuery(
                            tenant_id=user.tenant_id,
                            user_id=user.user_id,
                            data_source_id=data_source_id,
                            conversation_id=conversation_id,
                            question=question,
                            sql_text=state.sql,
                            result_summary=json.dumps({
                                "row_count": len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
                            }, ensure_ascii=False),
                            chart_config=state.chart_option,
                        )
                        db.add(saved)
                except Exception as e:
                    logger.warning("SavedQuery 写入失败 (不阻塞): %s", e)

            # Few-shot SQL 回流: 成功查询 → 向量库 (RAG-004, 失败不阻塞)
            if state.success and state.sql:
                try:
                    from app.services.fewshot import index_fewshot_example
                    from app.services.embedder import get_embedder
                    await index_fewshot_example(
                        question=question,
                        sql=state.sql,
                        embedder=get_embedder(),
                        data_source_id=data_source_id,
                    )
                except Exception:
                    logger.debug("fewshot 回流失败 (不阻塞)")

        except Exception as e:
            logger.warning("StateStore 持久化失败 (不阻塞): %s", e)

    # 审计
    # SEC-006: SQL 注入拦截专项标识 — Layer 1(AST/多语句/写操作) 或 Layer 2(危险函数) 失败
    # 视为注入拦截, 用 action="sql_injection_blocked" 单独标记便于检索
    _INJECTION_LAYERS = ("AST", "dangerous_function")
    is_injection_block = bool(
        state.error and any(f"({layer})" in state.error for layer in _INJECTION_LAYERS)
    )
    # DSO-07: 从执行结果取耗时, 判定慢查询 (阈值可配置)
    exec_duration = getattr(state.execute_result, "duration_ms", None) if state.execute_result else None
    is_slow = bool(
        exec_duration is not None
        and exec_duration >= get_settings().sql_slow_query_threshold * 1000
    )
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
    )
    await db.commit()

    # 客户端安全: 内部异常不泄露详情, 业务错误保留原文
    _client_error = "服务内部错误, 请稍后重试" if state.error_is_internal else state.error
    # 组装响应
    exec_result = state.execute_result
    return ChatResponse(
        success=state.success,
        conversation_id=conversation_id,
        intent=state.intent_output.intent if state.intent_output else None,
        question=_safe_response_question(state.intent_output, question),
        sql=state.sql or None,
        columns=list(exec_result.columns) if exec_result and hasattr(exec_result, "columns") else [],
        rows=[[normalize_value(v) for v in r] for r in exec_result.rows] if exec_result and hasattr(exec_result, "rows") else [],
        row_count=len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
        truncated=exec_result.truncated if exec_result and hasattr(exec_result, "truncated") else False,
        chart=state.chart_option,
        error=_client_error,
        reply=state.reply or None,
        ask_user=AskUserPayload(
            reason=str(state.ask_user_request.reason.value)
                if hasattr(state.ask_user_request.reason, "value")
                else str(state.ask_user_request.reason),
            question=state.ask_user_request.question,
            options=state.ask_user_request.options,
        ) if state.ask_user_request else None,
        stage=state.stage.value,
        llm_calls=state.llm_call_count,
        self_heal_rounds=state.self_heal_rounds,
        token_usage=TokenUsagePayload(**token_stats) if token_stats else None,
        fewshot_count=state.fewshot_count,
        degraded=state.degraded,
    )
