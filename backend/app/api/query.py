"""Query API — synchronous and SSE streaming query endpoints."""
import asyncio
import json
import re
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig, SavedQuery
from app.core.config import settings
from app.core.security import get_current_user, require_role
from app.core.logging import get_logger
from app.schemas.query import QueryRequest, QueryResponse, ExplainRequest
from app.ai.graph import build_graph
from app.ai.chart_type import infer_chart_type
from app.services.cache_service import cache_get, cache_set

logger = get_logger(__name__)

router = APIRouter(prefix="/query", tags=["查询"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


async def _check_datasource(datasource_id: str, tenant_id: str, db: AsyncSession) -> DataSource:
    """Verify datasource exists, belongs to tenant, and is active."""
    result = await db.execute(
        select(DataSource).where(
            DataSource.id == datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )
    if not ds.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("DATASOURCE_INACTIVE", "数据源已禁用，请联系管理员启用"),
        )
    return ds


async def _auto_save_history(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    datasource_id: str,
    question: str,
    sql: str | None,
    success: bool,
    row_count: int = 0,
    execution_time_ms: int | None = None,
    error: str | None = None,
    chart_type: str | None = None,
) -> None:
    """Auto-save every query execution to history."""
    sq = SavedQuery(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        datasource_id=datasource_id,
        name=None,
        query_text=question,
        generated_sql=sql or "",
        success=success,
        row_count=row_count,
        execution_time_ms=execution_time_ms,
        error=error,
        chart_type=chart_type,
    )
    db.add(sq)


@router.post("", response_model=QueryResponse)
async def create_query(
    data: QueryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = time.monotonic()
    tenant_id = user["tenant_id"]

    ds = await _check_datasource(data.datasource_id, tenant_id, db)

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

    # Build graph and execute
    try:
        graph = build_graph()
        initial_state = {
            "question": data.question,
            "datasource_id": data.datasource_id,
            "tenant_id": tenant_id,
            "conversation_history": data.history or [],
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

        # Mask sensitive data
        from app.services.data_masking import mask_sensitive_data
        columns, rows = mask_sensitive_data(columns, rows)

        # Audit log with structured fields
        from app.services.audit_service import log_action
        from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
        await log_action(
            db, tenant_id, user["user_id"],
            "QUERY_EXECUTE", "query", data.datasource_id,
            details=f"question={data.question[:200]} intent={final_state.get('intent')}",
            sql_text=final_state.get("sql"),
            result_count=final_state.get("row_count", 0),
            execution_time_ms=elapsed_ms,
            error_message=final_state.get("error") if not final_state.get("success") else None,
        )
        event_name = EVENT_QUERY_SUCCESS if final_state.get("success") else EVENT_QUERY_ERROR
        await track_event(db, tenant_id, user["user_id"], event_name, {
            "question": data.question,
            "success": final_state.get("success"),
        })

        # Auto-save query history
        await _auto_save_history(
            db, tenant_id, user["user_id"], data.datasource_id,
            data.question, final_state.get("sql"),
            success=final_state.get("success", False),
            row_count=final_state.get("row_count", 0),
            execution_time_ms=elapsed_ms,
            error=final_state.get("error"),
            chart_type=chart_type,
        )
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
    """SSE 流式查询：逐步推送 intent/schema/sql/data/结果。"""
    tenant_id = user["tenant_id"]
    ds = await _check_datasource(data.datasource_id, tenant_id, db)

    start_time = time.monotonic()
    final_success = False
    final_sql = None
    final_error = None
    final_row_count = 0

    async def event_stream():
        nonlocal final_success, final_sql, final_error, final_row_count
        try:
            # Step 1: Intent
            from app.ai.nodes.intent import classify_intent
            intent = await classify_intent(data.question)
            intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
            yield f"event: intent\ndata: {json.dumps({'intent': intent, 'detail': f'识别为{intent_label}意图'}, ensure_ascii=False)}\n\n"

            if intent != "DataQuery":
                final_error = "请提出数据查询相关的问题"
                yield f"event: complete\ndata: {json.dumps({'success': False, 'error': final_error}, ensure_ascii=False)}\n\n"
                return

            # Step 2: Schema selection (LLM two-step: table selection + column selection)
            from app.ai.nodes.schema_selection import schema_selection_node
            state = {
                "question": data.question,
                "datasource_id": data.datasource_id,
                "tenant_id": tenant_id,
            }
            schema_result = await schema_selection_node(state)
            schema_context = schema_result.get("schema_context", "")
            raw_metadata = schema_result.get("raw_metadata", "")

            # Extract selected tables from schema context for display
            selected_tables = re.findall(r'^表名:\s*(\S+)', schema_context, re.MULTILINE)
            schema_detail = f"选择了 {len(selected_tables)} 个表: {', '.join(selected_tables)}" if selected_tables else "未找到相关表"
            yield f"event: semantics\ndata: {json.dumps({'intent': 'schema_selected', 'detail': schema_detail}, ensure_ascii=False)}\n\n"

            # Step 3: SQL generation
            from app.ai.nodes.generation import generate_sql
            sql = await generate_sql(data.question, schema_context, raw_metadata=raw_metadata, history=data.history)
            final_sql = sql
            sql_detail = f"生成 SQL: {sql[:80]}..." if sql and len(sql) > 80 else f"生成 SQL: {sql or '空'}"
            yield f"event: sql\ndata: {json.dumps({'sql': sql, 'detail': sql_detail}, ensure_ascii=False)}\n\n"

            if not sql:
                final_error = "无法生成 SQL"
                yield f"event: complete\ndata: {json.dumps({'success': False, 'error': final_error}, ensure_ascii=False)}\n\n"
                return

            # Step 4: Execute
            from app.ai.nodes.execution import execute_sql
            exec_result = await execute_sql(sql, data.datasource_id, tenant_id=tenant_id)
            final_success = exec_result.get("success", False)
            final_row_count = exec_result.get("row_count", 0)
            final_error = exec_result.get("error")
            exec_detail = f"查询成功，返回 {final_row_count} 行" if final_success else f"执行失败: {final_error}"
            yield f"event: data\ndata: {json.dumps({**exec_result, 'detail': exec_detail}, ensure_ascii=False, default=str)}\n\n"

            # Step 5: Self-heal on failure
            if not final_success and schema_context:
                from app.ai.nodes.self_heal import self_heal_sql
                heal_result = await self_heal_sql(
                    question=data.question,
                    sql=sql,
                    error=final_error or "",
                    datasource_id=data.datasource_id,
                    schema_context=schema_context,
                    dialect="mysql",
                )
                if heal_result.get("success"):
                    final_sql = heal_result.get("sql", final_sql)
                    exec_result = await execute_sql(final_sql, data.datasource_id, tenant_id=tenant_id)
                    final_success = exec_result.get("success", False)
                    final_row_count = exec_result.get("row_count", 0)
                    final_error = exec_result.get("error")
                    heal_detail = f"自愈成功，修正后 SQL: {final_sql[:60]}..." if len(final_sql) > 60 else f"自愈成功，修正后 SQL: {final_sql}"
                    yield f"event: sql\ndata: {json.dumps({'sql': final_sql, 'detail': heal_detail}, ensure_ascii=False)}\n\n"
                    exec_detail = f"查询成功，返回 {final_row_count} 行" if final_success else f"执行失败: {final_error}"
                    yield f"event: data\ndata: {json.dumps({**exec_result, 'detail': exec_detail}, ensure_ascii=False, default=str)}\n\n"

            # Step 6: Chart type
            if exec_result.get("rows") and exec_result.get("columns"):
                chart_type = infer_chart_type(exec_result["columns"], exec_result["rows"])
                chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}"
                yield f"event: chart\ndata: {json.dumps({'chart_type': chart_type, 'detail': chart_detail}, ensure_ascii=False)}\n\n"

            yield f"event: complete\ndata: {json.dumps({'success': final_success}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception("Stream query error")
            final_error = str(e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        # Audit + auto-save after stream completes
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        try:
            from app.services.audit_service import log_action
            await log_action(
                db, tenant_id, user["user_id"],
                "QUERY_STREAM", "query", data.datasource_id,
                details=f"question={data.question[:200]}",
                sql_text=final_sql,
                result_count=final_row_count,
                execution_time_ms=elapsed_ms,
                error_message=final_error if not final_success else None,
            )
            await _auto_save_history(
                db, tenant_id, user["user_id"], data.datasource_id,
                data.question, final_sql,
                success=final_success,
                row_count=final_row_count,
                execution_time_ms=elapsed_ms,
                error=final_error,
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to save stream query audit/history")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@router.post("/explain")
async def explain_sql(
    data: ExplainRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回 SQL 的自然语言解释。"""
    from app.ai.nodes.sql_explainer import explain_sql as do_explain
    from app.services.audit_service import log_action

    explanation = await do_explain(data.sql)

    await log_action(
        db, user["tenant_id"], user["user_id"],
        "SQL_EXPLAIN", "query", "",
        details=f"sql={data.sql[:200]}",
        sql_text=data.sql,
    )
    await db.commit()

    return {"explanation": explanation}


@router.post("/export", dependencies=[Depends(require_role("admin", "user"))])
async def export_csv(
    data: QueryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出查询结果为 CSV（read_only 角色不可用）。"""
    import csv
    import io
    from fastapi import Response

    tenant_id = user["tenant_id"]
    ds = await _check_datasource(data.datasource_id, tenant_id, db)

    # Execute query to get data
    graph = build_graph()
    initial_state = {
        "question": data.question,
        "datasource_id": data.datasource_id,
        "tenant_id": tenant_id,
    }
    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=settings.query_pipeline_timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=_error("TIMEOUT", f"查询超时（{settings.query_pipeline_timeout}秒限制）"),
        )

    columns = final_state.get("columns", [])
    rows = final_state.get("rows", [])

    if not columns or not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NO_DATA", "无数据可导出"),
        )

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([str(row.get(col, "")) for col in columns])

    csv_content = output.getvalue()
    output.close()

    # Audit
    from app.services.audit_service import log_action
    await log_action(
        db, tenant_id, user["user_id"],
        "QUERY_EXPORT", "query", data.datasource_id,
        details=f"question={data.question[:200]} rows={len(rows)}",
    )
    await db.commit()

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=export.csv"},
    )