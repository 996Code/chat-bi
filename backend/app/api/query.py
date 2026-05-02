import asyncio
import json
import time
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.schemas.query import QueryRequest, QueryResponse
from app.ai.graph import build_graph
from app.ai.chart_type import infer_chart_type

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
    from sqlalchemy import select

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

    # Get schema context from metadata_configs
    config_result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == data.datasource_id,
            MetadataConfig.tenant_id == tenant_id,
        )
    )
    config = config_result.scalar_one_or_none()
    schema_context = config.config if config else ""

    # Build graph and execute
    try:
        graph = build_graph()
        initial_state = {
            "question": data.question,
            "datasource_id": data.datasource_id,
            "schema_context": schema_context,
        }

        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=35.0,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Infer chart type from data
        chart_type = "none"
        columns = final_state.get("columns", [])
        rows = final_state.get("rows", [])
        if rows and columns:
            chart_type = infer_chart_type(columns, rows)

        response = QueryResponse(
            success=final_state.get("success", False),
            intent=final_state.get("intent"),
            sql=final_state.get("sql"),
            columns=columns,
            rows=rows,
            row_count=final_state.get("row_count", 0),
            error=final_state.get("error"),
            execution_time_ms=final_state.get("execution_time_ms") or elapsed_ms,
        )
        return response

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error="查询超时（35秒限制）",
            execution_time_ms=elapsed_ms,
        )
    except Exception as e:
        logger.exception("Query pipeline error: %s", e)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error=f"查询失败: {e}",
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

    from sqlalchemy import select

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
    schema_context = config.config if config else ""

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
