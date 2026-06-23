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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import AgentDeps, AgentState, run_agent
from app.core.auth import AuthUser, require_user, write_audit_log
from app.core.llm_client import get_llm_client
from app.db.models import DataSource, SemanticModel
from app.db.session import get_db
from app.schemas.semantic_layer import SemanticModelContent
from app.services.datasource_engine import datasource_to_url, get_engine_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """问答请求。"""
    question: str
    data_source_id: str | None = None  # None → 用用户默认/第一个数据源
    conversation_id: str | None = None  # None → 新对话; 有值 → 追问 (恢复上下文)


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
    ask_user: dict | None = None  # 需要用户澄清时的提问
    # 过程信息 (调试/可观测)
    stage: str = ""
    llm_calls: int = 0
    self_heal_rounds: int = 0


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
    from app.ai.ask_user import should_ask_for_schema, should_ask_for_result

    llm = get_llm_client()
    store = get_vector_store()

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
    url = datasource_to_url(ds) if ds else ""

    deps = AgentDeps(
        classify_intent=lambda q: classify_intent(q, llm),
        retrieve=lambda q: _retrieve(q, store, _get_embedder(), llm_client=llm, data_source_id=data_source_id),
        think=lambda q, ctx, models: think(q, ctx, models, llm),
        generate_sql=lambda question, schema_context, allowed_columns, llm_client=None, **kw: generate_sql(
            question=question, schema_context=schema_context,
            allowed_columns=allowed_columns, llm_client=llm,
        ),
        execute_sql=lambda sql: execute_sql(sql, data_source_id, url, get_engine_pool()),
        heal_sql=lambda sql, error, allowed_columns, schema_context, **kw: heal_sql(
            sql=sql, error=error, allowed_columns=allowed_columns,
            schema_context=schema_context, llm_client=llm,
        ),
        check_result=lambda rows, columns, sql: check_result(rows, columns, sql),
        generate_chart=lambda question, columns, rows, chart_type_hint=None, **kw: generate_chart(
            question=question, columns=columns, rows=rows,
            llm_client=llm, chart_type_hint=chart_type_hint,
        ),
        should_ask_for_schema=should_ask_for_schema,
        should_ask_for_result=should_ask_for_result,
    )
    return deps, content


def _get_embedder():
    """惰性获取 embedder (避免循环 import)。"""
    from app.services.embedder import get_embedder
    return get_embedder()


@router.post("", response_model=ChatResponse)
async def chat(
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
        raise HTTPException(status_code=500, detail=f"Agent 初始化失败: {e}")

    # 运行 Agent
    state = AgentState(question=req.question, semantic_content=content)
    state = await run_agent(state, deps)

    # 持久化 Agent 状态到 Checkpointer (对标 AEE-005, 支持追问恢复)
    import uuid
    conversation_id = req.conversation_id or str(uuid.uuid4()).replace("-", "")[:32]
    try:
        from app.core.checkpointer import get_checkpointer
        get_checkpointer().save_turn(
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            turn_data={
                "question": req.question,
                "intent": state.intent_output.intent if state.intent_output else None,
                "sql": state.sql,
                "success": state.success,
                "error": state.error,
                "stage": state.stage.value,
            },
        )
    except Exception as e:
        logger.warning("Checkpointer 持久化失败 (不阻塞): %s", e)

    # 审计
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="chat", action="query",
        status="success" if state.success else "fail",
        sql_text=state.sql or None,
        error_message=state.error[:500] if state.error else None,
    )
    await db.commit()

    # 组装响应
    exec_result = state.execute_result
    return ChatResponse(
        success=state.success,
        conversation_id=conversation_id,
        intent=state.intent_output.intent if state.intent_output else None,
        question=state.intent_output.normalized_question if state.intent_output else req.question,
        sql=state.sql or None,
        columns=list(exec_result.columns) if exec_result and hasattr(exec_result, "columns") else [],
        rows=[list(r) for r in exec_result.rows] if exec_result and hasattr(exec_result, "rows") else [],
        row_count=len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
        truncated=exec_result.truncated if exec_result and hasattr(exec_result, "truncated") else False,
        chart=state.chart_option,
        error=state.error,
        ask_user={
            "reason": state.ask_user_request.reason,
            "question": state.ask_user_request.question,
            "options": state.ask_user_request.options,
        } if state.ask_user_request else None,
        stage=state.stage.value,
        llm_calls=state.llm_call_count,
        self_heal_rounds=state.self_heal_rounds,
    )
