"""Query API — synchronous and SSE streaming query endpoints."""
import asyncio
import json
import re
import time
import uuid
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig, SavedQuery, AsyncQuery
from app.core.config import settings
from app.core.security import get_current_user, require_role
from app.core.logging import get_logger
from app.schemas.query import QueryRequest, QueryResponse, ExplainRequest, AsyncQueryResponse, AsyncQueryStatus
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

    # Check exact-match cache first
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

    # Fallback: semantic cache
    from app.services.cache_service import semantic_cache_get
    sem_cached = await semantic_cache_get(data.question, data.datasource_id, tenant_id)
    if sem_cached:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        from app.services.analytics_service import track_event, EVENT_QUERY_SUCCESS
        await track_event(db, tenant_id, user["user_id"], EVENT_QUERY_SUCCESS, {
            "question": data.question,
            "cached": True,
            "semantic": True,
        })
        await db.commit()
        return QueryResponse(
            success=sem_cached.get("success", False),
            intent=sem_cached.get("intent"),
            sql=sem_cached.get("sql"),
            columns=sem_cached.get("columns", []),
            rows=sem_cached.get("rows", []),
            row_count=sem_cached.get("row_count", 0),
            error=sem_cached.get("error"),
            execution_time_ms=elapsed_ms,
            chart_type=sem_cached.get("chart_type", "table"),
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

        # Find conversation_id for audit log linkage
        conv_id = None
        try:
            from app.db.models import Conversation
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(minutes=2)
            conv_stmt = (
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.user_id == user["user_id"],
                    Conversation.datasource_id == data.datasource_id,
                    Conversation.updated_at >= cutoff,
                )
                .order_by(desc(Conversation.updated_at))
                .limit(1)
            )
            conv_result = await db.execute(conv_stmt)
            conv_row = conv_result.scalar_one_or_none()
            if conv_row:
                conv_id = str(conv_row.id)
        except Exception:
            pass

        # Audit log with structured fields
        from app.services.audit_service import log_action
        from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
        sql_exec_ms = final_state.get("execution_time_ms")  # Pure SQL execution time from execute_sql node
        await log_action(
            db, tenant_id, user["user_id"],
            "QUERY_EXECUTE", "query", data.datasource_id,
            details=f"question={data.question[:200]} intent={final_state.get('intent')}",
            sql_text=final_state.get("sql"),
            result_count=final_state.get("row_count", 0),
            execution_time_ms=elapsed_ms,  # Total pipeline time
            sql_execution_time_ms=sql_exec_ms,  # Pure SQL execution time
            error_message=final_state.get("error") if not final_state.get("success") else None,
            conversation_id=conv_id,
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
    final_rows = []
    final_columns = []
    cache_hit = False
    cache_type = None
    sql_execution_ms = None  # Pure SQL execution time

    async def event_stream():
        nonlocal final_success, final_sql, final_error, final_row_count, final_rows, final_columns, cache_hit, cache_type

        # Step 0: Check cache
        step_start = time.monotonic()
        cached = await cache_get(data.question, data.datasource_id, tenant_id)
        if cached:
            cache_hit = True
            cache_type = "exact"
            cache_duration = int((time.monotonic() - step_start) * 1000)
            yield f"event: cache\ndata: {json.dumps({'hit': True, 'type': 'exact', 'duration_ms': cache_duration}, ensure_ascii=False)}\n\n"
            # Cache hit: still run full pipeline (intent → schema → execute SQL)
            # Step 1: Intent
            intent_start = time.monotonic()
            from app.ai.nodes.intent import classify_intent
            intent = await classify_intent(data.question)
            intent_duration = int((time.monotonic() - intent_start) * 1000)
            intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
            yield f"event: intent\ndata: {json.dumps({'intent': intent, 'detail': f'识别为{intent_label}意图（关键词匹配）', 'duration_ms': intent_duration, 'method': 'cached'}, ensure_ascii=False)}\n\n"

            if intent != "DataQuery":
                friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
                final_error = friendly_msg
                yield f"event: complete\ndata: {json.dumps({'success': False, 'error': friendly_msg}, ensure_ascii=False)}\n\n"
                return

            # Step 2: Schema selection (from cached info)
            schema_start = time.monotonic()
            cached_sql = cached.get("sql")
            schema_duration = int((time.monotonic() - schema_start) * 1000)
            # Extract table names from SQL
            from_match = re.findall(r'FROM\s+(\w+)', cached_sql or '', re.IGNORECASE)
            join_match = re.findall(r'JOIN\s+(\w+)', cached_sql or '', re.IGNORECASE)
            selected_tables = list(dict.fromkeys(from_match + join_match))
            # Map columns to tables (cache stores flat column list, distribute evenly)
            cached_cols = cached.get("columns", [])
            if selected_tables:
                per_table = max(1, len(cached_cols) // len(selected_tables))
                selected_columns = {}
                for i, t in enumerate(selected_tables):
                    start_idx = i * per_table
                    selected_columns[t] = cached_cols[start_idx:start_idx + per_table]
            else:
                selected_columns = {"columns": cached_cols[:15]}
            table_detail = f"选择了 {len(selected_tables)} 个表: {', '.join(selected_tables)}" if selected_tables else f"使用缓存 Schema（{len(cached_cols)} 个字段）"
            yield f"event: semantics\ndata: {json.dumps({'detail': table_detail, 'tables': selected_tables, 'columns': selected_columns, 'duration_ms': schema_duration, 'source': 'cached'}, ensure_ascii=False)}\n\n"

            # Step 3: SQL (use cached SQL, verify with AST)
            sql_start = time.monotonic()
            if cached_sql:
                # AST validation on cached SQL
                import sqlglot
                try:
                    parsed = sqlglot.parse_one(cached_sql, dialect="mysql")
                    stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
                    if stmt_type != "SELECT":
                        yield f"event: sql\ndata: {json.dumps({'sql': cached_sql, 'detail': f'缓存 SQL 非 SELECT 语句 ({stmt_type})，拒绝执行', 'duration_ms': 0, 'validation': {'rejected': True}}, ensure_ascii=False)}\n\n"
                        final_error = f"缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
                        yield f"event: complete\ndata: {json.dumps({'success': False, 'error': final_error}, ensure_ascii=False)}\n\n"
                        return
                except Exception as ast_err:
                    yield f"event: sql\ndata: {json.dumps({'sql': cached_sql, 'detail': f'缓存 SQL 解析失败: {str(ast_err)}', 'duration_ms': 0}, ensure_ascii=False)}\n\n"

                sql_duration = int((time.monotonic() - sql_start) * 1000)
                final_sql = cached_sql
                yield f"event: sql\ndata: {json.dumps({'sql': cached_sql, 'detail': f'使用缓存 SQL（精确缓存命中，{cache_duration}ms）', 'duration_ms': sql_duration, 'source': 'cached'}, ensure_ascii=False)}\n\n"
            else:
                # No cached SQL — run full generation pipeline
                pass  # fall through to cache miss path

            if not cached_sql:
                # Fall through to normal pipeline below
                cache_hit = False
                cache_type = None
            else:
                # Step 4: Execute cached SQL
                exec_start = time.monotonic()
                from app.ai.nodes.execution import execute_sql
                exec_result = await execute_sql(cached_sql, data.datasource_id, tenant_id=tenant_id)
                exec_duration = int((time.monotonic() - exec_start) * 1000)
                sql_execution_ms = exec_duration
                final_success = exec_result.get("success", False)
                final_row_count = exec_result.get("row_count", 0)
                final_rows = exec_result.get("rows", [])
                final_columns = exec_result.get("columns", [])
                final_error = exec_result.get("error")
                exec_detail = f"查询成功，返回 {final_row_count} 行（缓存 SQL 执行，{exec_duration}ms）" if final_success else f"执行失败: {final_error}"
                yield f"event: data\ndata: {json.dumps({**exec_result, 'detail': exec_detail, 'duration_ms': exec_duration}, ensure_ascii=False, default=str)}\n\n"

                # If cached SQL failed, try full regeneration
                if not final_success:
                    from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
                    error_code = extract_error_code(final_error or "")
                    schema_ctx = ""
                    if selected_tables:
                        schema_ctx = f"Tables: {', '.join(selected_tables)}"
                    heal_result = await self_heal_sql(
                        question=data.question,
                        sql=cached_sql,
                        error=final_error or "",
                        datasource_id=data.datasource_id,
                        schema_context=schema_ctx,
                        dialect="mysql",
                    )
                    if heal_result.get("success"):
                        final_sql = heal_result.get("sql", cached_sql)
                        heal_exec_start = time.monotonic()
                        exec_result2 = await execute_sql(final_sql, data.datasource_id, tenant_id=tenant_id)
                        sql_execution_ms = int((time.monotonic() - heal_exec_start) * 1000)
                        final_success = exec_result2.get("success", False)
                        final_row_count = exec_result2.get("row_count", 0)
                        final_rows = exec_result2.get("rows", [])
                        final_columns = exec_result2.get("columns", [])
                        final_error = exec_result2.get("error")
                        yield f"event: sql\ndata: {json.dumps({'sql': final_sql, 'detail': f'自愈成功，修正后 SQL: {final_sql[:60]}...', 'error_code': error_code, 'retry': 1}, ensure_ascii=False)}\n\n"
                        exec_detail = f"查询成功，返回 {final_row_count} 行（自愈执行，{sql_execution_ms}ms）" if final_success else f"执行失败: {final_error}"
                        yield f"event: data\ndata: {json.dumps({**exec_result2, 'detail': exec_detail, 'duration_ms': sql_execution_ms}, ensure_ascii=False, default=str)}\n\n"

                # Step 5: Chart type inference
                if final_rows and final_columns:
                    chart_type = infer_chart_type(final_columns, final_rows)
                    chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                    chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（缓存数据）"
                    yield f"event: chart\ndata: {json.dumps({'chart_type': chart_type, 'detail': chart_detail}, ensure_ascii=False)}\n\n"

                total_ms = int((time.monotonic() - start_time) * 1000)
                is_slow = total_ms > settings.slow_query_threshold_ms
                yield f"event: complete\ndata: {json.dumps({'success': final_success, 'is_slow': is_slow, 'cached': True, 'cache_type': 'exact'}, ensure_ascii=False)}\n\n"
                return

        from app.services.cache_service import semantic_cache_get
        sem_cached = await semantic_cache_get(data.question, data.datasource_id, tenant_id)
        if sem_cached:
            cache_hit = True
            cache_type = "semantic"
            cache_duration = int((time.monotonic() - step_start) * 1000)
            yield f"event: cache\ndata: {json.dumps({'hit': True, 'type': 'semantic', 'duration_ms': cache_duration}, ensure_ascii=False)}\n\n"
            # Semantic cache hit: run full pipeline with cached SQL
            sem_sql = sem_cached.get("sql")
            if not sem_sql:
                # No SQL in semantic cache, fall through
                cache_hit = False
                cache_type = None
            else:
                # Step 1: Intent
                intent_start = time.monotonic()
                from app.ai.nodes.intent import classify_intent
                intent = await classify_intent(data.question)
                intent_duration = int((time.monotonic() - intent_start) * 1000)
                intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
                yield f"event: intent\ndata: {json.dumps({'intent': intent, 'detail': f'识别为{intent_label}意图（语义缓存命中）', 'duration_ms': intent_duration}, ensure_ascii=False)}\n\n"

                if intent != "DataQuery":
                    friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
                    final_error = friendly_msg
                    yield f"event: complete\ndata: {json.dumps({'success': False, 'error': friendly_msg}, ensure_ascii=False)}\n\n"
                    return

                # Step 2: Schema
                schema_start = time.monotonic()
                sem_tables = sem_cached.get("tables") or []
                schema_duration = int((time.monotonic() - schema_start) * 1000)
                if not sem_tables:
                    sem_tables = re.findall(r'FROM\s+(\w+)', sem_sql or '', re.IGNORECASE)
                    sem_tables = list(dict.fromkeys(sem_tables))
                sem_cols_raw = sem_cached.get("columns", [])
                if sem_tables:
                    per_table = max(1, len(sem_cols_raw) // len(sem_tables))
                    sem_cols = {t: sem_cols_raw[i*per_table:(i+1)*per_table] for i, t in enumerate(sem_tables)}
                else:
                    sem_cols = {"columns": sem_cols_raw[:15]}
                table_detail = f"选择了 {len(sem_tables)} 个表: {', '.join(sem_tables)}" if sem_tables else f"语义缓存 Schema（{len(sem_cols_raw)} 个字段）"
                yield f"event: semantics\ndata: {json.dumps({'detail': table_detail, 'tables': sem_tables, 'columns': sem_cols, 'duration_ms': schema_duration, 'source': 'semantic_cached'}, ensure_ascii=False)}\n\n"

                # Step 3: SQL
                sql_start = time.monotonic()
                import sqlglot
                try:
                    parsed = sqlglot.parse_one(sem_sql, dialect="mysql")
                    stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
                    if stmt_type != "SELECT":
                        final_error = f"语义缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
                        yield f"event: complete\ndata: {json.dumps({'success': False, 'error': final_error}, ensure_ascii=False)}\n\n"
                        return
                except Exception:
                    pass

                sql_duration = int((time.monotonic() - sql_start) * 1000)
                final_sql = sem_sql
                yield f"event: sql\ndata: {json.dumps({'sql': sem_sql, 'detail': f'使用缓存 SQL（语义缓存命中，{cache_duration}ms）', 'duration_ms': sql_duration, 'source': 'semantic_cached'}, ensure_ascii=False)}\n\n"

                # Step 4: Execute
                exec_start = time.monotonic()
                from app.ai.nodes.execution import execute_sql
                exec_result = await execute_sql(sem_sql, data.datasource_id, tenant_id=tenant_id)
                exec_duration = int((time.monotonic() - exec_start) * 1000)
                sql_execution_ms = exec_duration
                final_success = exec_result.get("success", False)
                final_row_count = exec_result.get("row_count", 0)
                final_rows = exec_result.get("rows", [])
                final_columns = exec_result.get("columns", [])
                final_error = exec_result.get("error")
                exec_detail = f"查询成功，返回 {final_row_count} 行（语义缓存 SQL 执行，{exec_duration}ms）" if final_success else f"执行失败: {final_error}"
                yield f"event: data\ndata: {json.dumps({**exec_result, 'detail': exec_detail, 'duration_ms': exec_duration}, ensure_ascii=False, default=str)}\n\n"

                # Self-heal if needed
                if not final_success:
                    from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
                    error_code = extract_error_code(final_error or "")
                    heal_result = await self_heal_sql(
                        question=data.question,
                        sql=sem_sql,
                        error=final_error or "",
                        datasource_id=data.datasource_id,
                        schema_context="",
                        dialect="mysql",
                    )
                    if heal_result.get("success"):
                        final_sql = heal_result.get("sql", sem_sql)
                        heal_exec_start = time.monotonic()
                        exec_result2 = await execute_sql(final_sql, data.datasource_id, tenant_id=tenant_id)
                        sql_execution_ms = int((time.monotonic() - heal_exec_start) * 1000)
                        final_success = exec_result2.get("success", False)
                        final_row_count = exec_result2.get("row_count", 0)
                        final_rows = exec_result2.get("rows", [])
                        final_columns = exec_result2.get("columns", [])
                        final_error = exec_result2.get("error")
                        yield f"event: sql\ndata: {json.dumps({'sql': final_sql, 'detail': f'自愈成功，修正后 SQL: {final_sql[:60]}...', 'error_code': error_code, 'retry': 1}, ensure_ascii=False)}\n\n"
                        exec_detail = f"查询成功，返回 {final_row_count} 行（自愈执行，{sql_execution_ms}ms）" if final_success else f"执行失败: {final_error}"
                        yield f"event: data\ndata: {json.dumps({**exec_result2, 'detail': exec_detail, 'duration_ms': sql_execution_ms}, ensure_ascii=False, default=str)}\n\n"

                # Step 5: Chart
                if final_rows and final_columns:
                    chart_type = infer_chart_type(final_columns, final_rows)
                    chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                    chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（语义缓存数据）"
                    yield f"event: chart\ndata: {json.dumps({'chart_type': chart_type, 'detail': chart_detail}, ensure_ascii=False)}\n\n"

                total_ms = int((time.monotonic() - start_time) * 1000)
                is_slow = total_ms > settings.slow_query_threshold_ms
                yield f"event: complete\ndata: {json.dumps({'success': final_success, 'is_slow': is_slow, 'cached': True, 'cache_type': 'semantic'}, ensure_ascii=False)}\n\n"
                return

        # Cache miss — use shared pipeline executor
        from app.services.pipeline_executor import execute_query_pipeline
        async for event in execute_query_pipeline(data.question, data.datasource_id, tenant_id, data.history):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
            # Track final values for audit/history
            if event["event"] == "sql" and event["data"].get("sql"):
                final_sql = event["data"]["sql"]
            elif event["event"] == "data":
                final_success = event["data"].get("success", False)
                final_row_count = event["data"].get("row_count", 0)
                final_rows = event["data"].get("rows", [])
                final_columns = event["data"].get("columns", [])
                final_error = event["data"].get("error")
                if event["data"].get("duration_ms"):
                    sql_execution_ms = event["data"]["duration_ms"]
            elif event["event"] == "complete":
                final_success = event["data"].get("success", final_success)
            elif event["event"] == "error":
                final_error = event["data"].get("error")

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
                sql_execution_time_ms=sql_execution_ms,
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

            # Cache successful stream result
            if final_success and final_rows:
                await cache_set(data.question, data.datasource_id, {
                    "success": True,
                    "intent": None,
                    "sql": final_sql,
                    "columns": final_columns,
                    "rows": final_rows,
                    "row_count": final_row_count,
                    "chart_type": None,
                }, tenant_id=tenant_id)

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


@router.post("/raw")
async def execute_raw_sql(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """直接执行 SQL（3.14 SQL 内联编辑）。仅允许 SELECT 查询。"""
    import time
    from app.ai.nodes.execution import validate_sql as do_validate
    from app.ai.chart_type import infer_chart_type
    from app.services.data_masking import mask_sensitive_data
    from app.services.audit_service import log_action
    from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR

    start = time.monotonic()
    tenant_id = user["tenant_id"]
    sql = data.get("sql", "").strip()
    datasource_id = data.get("datasource_id")

    if not sql:
        raise HTTPException(status_code=400, detail=_error("EMPTY_SQL", "SQL 不能为空"))

    ds = await _check_datasource(datasource_id, tenant_id, db)

    # Validate: only SELECT allowed
    validation = do_validate(sql)
    if not validation.get("safe", True):
        raise HTTPException(
            status_code=403,
            detail=_error("UNSAFE_SQL", f"仅允许 SELECT 查询: {validation.get('reason', '')}"),
        )

    # Execute
    from app.ai.nodes.execution import execute_sql
    result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    chart_type = "none"
    if rows and columns:
        chart_type = infer_chart_type(columns, rows)

    # Mask sensitive data
    columns, rows = mask_sensitive_data(columns, rows)

    event_name = EVENT_QUERY_SUCCESS if result.get("success") else EVENT_QUERY_ERROR
    await log_action(
        db, tenant_id, user["user_id"],
        "RAW_QUERY_EXECUTE", "query", datasource_id,
        details=f"sql={sql[:200]}",
        sql_text=sql,
        result_count=result.get("row_count", 0),
        execution_time_ms=elapsed_ms,
        error_message=result.get("error") if not result.get("success") else None,
    )
    await track_event(db, tenant_id, user["user_id"], event_name, {
        "question": f"RAW: {sql[:100]}",
        "success": result.get("success"),
    })
    await db.commit()

    return {
        "success": result.get("success", False),
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "row_count": result.get("row_count", 0),
        "error": result.get("error"),
        "execution_time_ms": elapsed_ms,
        "chart_type": chart_type,
    }


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


# ==================== Async Query Execution (PERF-03) ====================


@router.post("/async", response_model=AsyncQueryResponse)
async def submit_async_query(
    data: QueryRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交异步查询任务 — 立即返回 task_id，前端轮询 GET /query/async/{task_id} 获取结果。"""
    tenant_id = user["tenant_id"]
    ds = await _check_datasource(data.datasource_id, tenant_id, db)

    # Create pending async query record
    task_id = uuid.uuid4()
    aq = AsyncQuery(
        id=task_id,
        tenant_id=tenant_id,
        user_id=user["user_id"],
        datasource_id=data.datasource_id,
        question=data.question,
        status="pending",
    )
    db.add(aq)
    await db.commit()

    # Schedule background task (UUIDs → strings for safe serialization)
    background_tasks.add_task(
        _run_async_query,
        task_id=str(task_id),
        question=data.question,
        datasource_id=str(data.datasource_id),
        tenant_id=str(tenant_id),
        user_id=str(user["user_id"]),
        history=data.history,
    )

    return AsyncQueryResponse(
        task_id=str(task_id),
        status="pending",
        question=data.question,
        datasource_id=data.datasource_id,
    )


@router.get("/async/{task_id}", response_model=AsyncQueryStatus)
async def get_async_query_status(
    task_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """轮询异步查询任务状态和结果。"""
    result = await db.execute(
        select(AsyncQuery).where(
            AsyncQuery.id == task_id,
            AsyncQuery.tenant_id == user["tenant_id"],
        )
    )
    aq = result.scalar_one_or_none()
    if not aq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("TASK_NOT_FOUND", "异步查询任务不存在"),
        )

    status_resp = AsyncQueryStatus(
        task_id=str(aq.id),
        status=aq.status,
        question=aq.question,
        datasource_id=str(aq.datasource_id),
        sql=aq.generated_sql,
        error=aq.error,
        chart_type=aq.chart_type or "none",
        execution_time_ms=aq.execution_time_ms,
        created_at=str(aq.created_at) if aq.created_at else None,
        updated_at=str(aq.updated_at) if aq.updated_at else None,
    )

    # Parse rows/columns from JSON if done
    if aq.status == "done" and aq.columns and aq.rows:
        status_resp.columns = json.loads(aq.columns)
        status_resp.rows = json.loads(aq.rows)
        status_resp.row_count = aq.row_count or 0

    # Return pipeline trace and intent
    if aq.intent:
        status_resp.intent = aq.intent
    if aq.pipeline_trace:
        try:
            status_resp.pipeline_trace = json.loads(aq.pipeline_trace)
        except Exception:
            status_resp.pipeline_trace = []
    elif aq.status == "running":
        # Live progress from Redis while running
        try:
            from app.core.redis_client import get_redis as _get_redis
            redis = await _get_redis()
            raw = await redis.get(f"async_trace:{task_id}")
            if raw:
                status_resp.pipeline_trace = json.loads(raw)
        except Exception:
            pass

    return status_resp


@router.delete("/async/{task_id}")
async def cancel_async_query(
    task_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消一个 pending/running 的异步查询任务。"""
    result = await db.execute(
        select(AsyncQuery).where(
            AsyncQuery.id == task_id,
            AsyncQuery.tenant_id == user["tenant_id"],
        )
    )
    aq = result.scalar_one_or_none()
    if not aq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("TASK_NOT_FOUND", "异步查询任务不存在"),
        )
    if aq.status in ("done", "failed", "cancelled"):
        return {"message": f"任务已处于终止状态: {aq.status}"}

    aq.status = "cancelled"
    await db.commit()
    return {"message": "任务已取消"}


async def _run_async_query(
    task_id: str,
    question: str,
    datasource_id: str,
    tenant_id: str,
    user_id: str,
    history: list[dict] | None = None,
):
    """Background task: run the full query pipeline and save results."""
    import time

    # Re-import here to avoid circular imports at module level
    from app.db.session import async_session_factory
    from app.services.audit_service import log_action
    from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    start = time.monotonic()

    async with async_session_factory() as db:
        try:
            # Update status to running
            result = await db.execute(select(AsyncQuery).where(AsyncQuery.id == task_id))
            aq = result.scalar_one_or_none()
            if not aq or aq.status == "cancelled":
                return
            aq.status = "running"
            await db.commit()

            # Run full pipeline via shared executor, collecting step events.
            # After each event, save trace to Redis so frontend polling can show progress.
            from app.services.pipeline_executor import execute_query_pipeline
            from app.core.redis_client import get_redis as _get_redis
            pipeline_trace = []
            pipeline_intent = None
            sql = None
            columns = []
            rows = []
            row_count = 0
            error = None
            final_success = False
            chart_type = "none"

            async def _save_progress():
                """Save current trace to Redis for polling progress updates."""
                try:
                    redis = await _get_redis()
                    await redis.setex(
                        f"async_trace:{task_id}",
                        settings.query_pipeline_timeout,
                        json.dumps(pipeline_trace, ensure_ascii=False, default=str),
                    )
                except Exception:
                    pass

            async for event in execute_query_pipeline(question, datasource_id, tenant_id, history):
                pipeline_trace.append(event)
                if event["event"] == "intent" and not pipeline_intent:
                    pipeline_intent = event["data"].get("intent")
                if event["event"] == "sql" and event["data"].get("sql"):
                    sql = event["data"]["sql"]
                if event["event"] == "data":
                    success = event["data"].get("success", False)
                    columns = event["data"].get("columns", [])
                    rows = event["data"].get("rows", [])
                    row_count = event["data"].get("row_count", 0)
                    error = event["data"].get("error")
                    final_success = success
                if event["event"] == "chart":
                    chart_type = event["data"].get("chart_type", "none")
                if event["event"] == "complete":
                    final_success = event["data"].get("success", final_success)
                if event["event"] == "error":
                    error = event["data"].get("error")
                    final_success = False

                # Save progress after each step
                await _save_progress()

            elapsed_ms = int((time.monotonic() - start) * 1000)

            # Update async query record
            result2 = await db.execute(select(AsyncQuery).where(AsyncQuery.id == task_id))
            aq2 = result2.scalar_one_or_none()
            if not aq2:
                return

            aq2.status = "done" if final_success else "failed"
            aq2.generated_sql = sql
            aq2.columns = json.dumps(columns, ensure_ascii=False, default=str) if columns else None
            aq2.rows = json.dumps(rows, ensure_ascii=False, default=str) if rows else None
            aq2.row_count = row_count
            aq2.error = error if not final_success else None
            aq2.chart_type = chart_type
            aq2.execution_time_ms = elapsed_ms
            aq2.intent = pipeline_intent
            aq2.pipeline_trace = json.dumps(pipeline_trace, ensure_ascii=False, default=str) if pipeline_trace else None

            # Audit log
            await log_action(
                db, tenant_id, user_id,
                "ASYNC_QUERY_EXECUTE", "query", datasource_id,
                details=f"question={question[:200]} task_id={task_id}",
                sql_text=sql,
                result_count=row_count,
                execution_time_ms=elapsed_ms,
                error_message=error if not final_success else None,
            )

            event_name = EVENT_QUERY_SUCCESS if final_success else EVENT_QUERY_ERROR
            await track_event(db, tenant_id, user_id, event_name, {
                "question": question,
                "async": True,
                "task_id": task_id,
            })

            # Auto-save history
            await _auto_save_history(
                db, tenant_id, user_id, datasource_id,
                question, sql,
                success=final_success,
                row_count=row_count,
                execution_time_ms=elapsed_ms,
                error=error,
                chart_type=chart_type,
            )

            # Cache successful result
            if final_success and rows:
                await cache_set(question, datasource_id, {
                    "success": True,
                    "intent": pipeline_intent,
                    "sql": sql,
                    "columns": columns,
                    "rows": rows,
                    "row_count": row_count,
                    "chart_type": chart_type,
                }, tenant_id=tenant_id)

            await db.commit()

        except asyncio.TimeoutError:
            logger.warning("Async query %s timed out after %ds", task_id, settings.query_pipeline_timeout)
            try:
                result3 = await db.execute(select(AsyncQuery).where(AsyncQuery.id == task_id))
                aq3 = result3.scalar_one_or_none()
                if aq3:
                    aq3.status = "failed"
                    aq3.error = f"查询超时（{settings.query_pipeline_timeout}秒限制）"
                    aq3.execution_time_ms = int((time.monotonic() - start) * 1000)
                    await db.commit()
            except Exception:
                pass
        except Exception as e:
            logger.exception("Async query %s failed: %s", task_id, e)
            try:
                result4 = await db.execute(select(AsyncQuery).where(AsyncQuery.id == task_id))
                aq4 = result4.scalar_one_or_none()
                if aq4:
                    aq4.status = "failed"
                    aq4.error = str(e)[:500]
                    aq4.execution_time_ms = int((time.monotonic() - start) * 1000)
                    await db.commit()
            except Exception:
                pass