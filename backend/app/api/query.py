import asyncio
import json
import time
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig
from app.core.config import settings
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.schemas.query import QueryRequest, QueryResponse
from app.ai.graph import build_graph
from app.ai.chart_type import infer_chart_type
from app.services.rag_schema_service import get_rag_schema
from app.services.cache_service import cache_get, cache_set
from app.ai.nodes.shared_utils import append_all_table_names

logger = get_logger(__name__)

router = APIRouter(prefix="/query", tags=["查询"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post("", response_model=QueryResponse)
async def create_query(
    data: QueryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = time.monotonic()
    tenant_id = user["tenant_id"]

    # Verify datasource belongs to tenant
    result = await db.execute(
        select(DataSource).where(
            DataSource.id == data.datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    # Check cache first
    cached = await cache_get(data.question, data.datasource_id, tenant_id)
    if cached:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        from app.services.analytics_service import track_event, EVENT_QUERY_SUCCESS
        await track_event(db, tenant_id, user["user_id"], EVENT_QUERY_SUCCESS, {
            "question": data.question,
            "cached": True,
        })
        await db.commit()
        return QueryResponse(
            success=cached.get("success", False),
            intent=cached.get("intent"),
            sql=cached.get("sql"),
            columns=cached.get("columns", []),
            rows=cached.get("rows", []),
            row_count=cached.get("row_count", 0),
            error=cached.get("error"),
            execution_time_ms=elapsed_ms,
            chart_type=cached.get("chart_type", "table"),
        )

    # Build graph and execute (RAG is handled inside the graph)
    try:
        graph = build_graph()
        initial_state = {
            "question": data.question,
            "datasource_id": data.datasource_id,
            "tenant_id": tenant_id,
        }

        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=settings.query_pipeline_timeout,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Infer chart type from data
        chart_type = "none"
        columns = final_state.get("columns", [])
        rows = final_state.get("rows", [])
        if rows and columns:
            chart_type = infer_chart_type(columns, rows)

        # Audit log
        from app.services.audit_service import log_action
        from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
        await log_action(
            db, tenant_id, user["user_id"],
            "QUERY_EXECUTE", "query", data.datasource_id,
            f"question={data.question[:200]} intent={final_state.get('intent')} success={final_state.get('success')}",
        )
        event_name = EVENT_QUERY_SUCCESS if final_state.get("success") else EVENT_QUERY_ERROR
        await track_event(db, tenant_id, user["user_id"], event_name, {
            "question": data.question,
            "success": final_state.get("success"),
        })
        await db.commit()

        response = QueryResponse(
            success=final_state.get("success", False),
            intent=final_state.get("intent"),
            sql=final_state.get("sql"),
            columns=columns,
            rows=rows,
            row_count=final_state.get("row_count", 0),
            error=final_state.get("error"),
            execution_time_ms=final_state.get("execution_time_ms") or elapsed_ms,
            chart_type=chart_type,
        )

        # Cache successful query results
        if final_state.get("success") and rows:
            await cache_set(data.question, data.datasource_id, {
                "success": True,
                "intent": final_state.get("intent"),
                "sql": final_state.get("sql"),
                "columns": columns,
                "rows": rows,
                "row_count": final_state.get("row_count", 0),
                "chart_type": chart_type,
            }, tenant_id=tenant_id)

        return response

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error=f"查询超时（{settings.query_pipeline_timeout}秒限制）",
            execution_time_ms=elapsed_ms,
        )
    except Exception as e:
        logger.exception("Query pipeline error: %s", e)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error="查询失败，请稍后重试",
            execution_time_ms=elapsed_ms,
        )


@router.post("/stream")
async def stream_query(
    data: QueryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE 流式查询：逐步推送 intent/sql/data/结果。"""
    tenant_id = user["tenant_id"]

    result = await db.execute(
        select(DataSource).where(
            DataSource.id == data.datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    config_result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == data.datasource_id,
            MetadataConfig.tenant_id == tenant_id,
        )
    )
    config = config_result.scalar_one_or_none()
    raw_metadata = config.config if config else ""
    schema_context = get_rag_schema(data.question, raw_metadata, datasource_id=data.datasource_id) if raw_metadata else ""
    schema_context = append_all_table_names(schema_context, raw_metadata)

    async def event_stream():
        try:
            # Step 1: Intent
            from app.ai.nodes.intent import classify_intent
            intent = await classify_intent(data.question)
            yield f"event: intent\ndata: {json.dumps({'intent': intent}, ensure_ascii=False)}\n\n"

            if intent != "DataQuery":
                yield f"event: complete\ndata: {json.dumps({'success': False, 'error': '请提出数据查询相关的问题'}, ensure_ascii=False)}\n\n"
                return

            # Step 2: SQL generation
            from app.ai.nodes.generation import generate_sql
            sql = await generate_sql(data.question, schema_context)
            yield f"event: sql\ndata: {json.dumps({'sql': sql}, ensure_ascii=False)}\n\n"

            if not sql:
                yield f"event: complete\ndata: {json.dumps({'success': False, 'error': '无法生成 SQL'}, ensure_ascii=False)}\n\n"
                return

            # Step 3: Execute
            from app.ai.nodes.execution import execute_sql
            exec_result = await execute_sql(sql, data.datasource_id)
            yield f"event: data\ndata: {json.dumps(exec_result, ensure_ascii=False, default=str)}\n\n"

            # Step 4: Chart type
            if exec_result.get("rows") and exec_result.get("columns"):
                chart_type = infer_chart_type(exec_result["columns"], exec_result["rows"])
                yield f"event: chart\ndata: {json.dumps({'chart_type': chart_type}, ensure_ascii=False)}\n\n"

            yield f"event: complete\ndata: {json.dumps({'success': exec_result.get('success', False)}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception("Stream query error")
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
