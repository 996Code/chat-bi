"""Evaluation service — runs AI pipeline queries with full trace.

Cache hit only caches SQL, query results are always fetched from real DB
to ensure data freshness. Non-DataQuery intent shows friendly error trace.
Source is tagged "eval" so slow query logs can distinguish from normal queries.
Every query is logged to audit table for monitoring.
"""
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def run_evaluation(
    db: AsyncSession,
    tenant_id: str,
    datasource_id: str,
    dataset: list[dict],
    evaluator_user_id: str,
) -> dict[str, Any]:
    """Run evaluation: for each question, invoke AI pipeline, capture full trace.

    Each item: {"question": "..."}
    """
    from app.ai.graph import build_graph
    from app.ai.nodes.execution import execute_sql
    from app.services.cache_service import cache_get, semantic_cache_get

    results = []
    sql_correct = 0
    total_time_ms = 0
    cache_hits = 0

    for item in dataset:
        question = item["question"]
        steps: list[dict] = []
        overall_start = time.monotonic()
        generated_sql = ""
        success = False
        rows: list = []
        columns: list = []
        row_count = 0
        error: str | None = None

        try:
            # Step 0: Cache check
            cache_step_start = time.monotonic()
            cached = await cache_get(question, datasource_id, tenant_id)
            cache_type: str | None = None
            if cached:
                cache_type = "exact"
            else:
                sem_cached = await semantic_cache_get(question, datasource_id, tenant_id)
                if sem_cached:
                    cache_type = "semantic"
            cache_ms = int((time.monotonic() - cache_step_start) * 1000)

            # --- Cache hit path: use cached SQL, execute against real DB ---
            if cache_type:
                cached_data = cached if cache_type == "exact" else sem_cached
                cached_sql = cached_data.get("sql", "")

                if cached_sql:
                    cache_hits += 1
                    steps.append({
                        "type": "cache",
                        "label": f"{'语义缓存' if cache_type == 'semantic' else '精确缓存'}命中",
                        "status": "done",
                        "detail": "SQL 缓存命中，复用缓存 SQL",
                        "duration_ms": cache_ms,
                        "cached": True,
                        "cache_type": cache_type,
                    })

                    steps.append({
                        "type": "intent",
                        "label": "意图识别: 数据查询",
                        "status": "done",
                        "detail": "使用缓存 SQL 跳过意图识别",
                    })

                    steps.append({
                        "type": "sql",
                        "label": "SQL 生成",
                        "status": "done",
                        "detail": "使用缓存 SQL",
                        "sql": cached_sql,
                    })

                    # Execute against real DB for fresh results
                    exec_start = time.monotonic()
                    exec_result = await execute_sql(cached_sql, datasource_id, tenant_id=tenant_id)
                    exec_ms = int((time.monotonic() - exec_start) * 1000)
                    generated_sql = cached_sql
                    success = exec_result.get("success", False)
                    rows = exec_result.get("rows", [])
                    columns = exec_result.get("columns", [])
                    row_count = exec_result.get("row_count", 0)
                    error = exec_result.get("error")

                    steps.append({
                        "type": "data",
                        "label": "执行查询",
                        "status": "done" if success else "failed",
                        "detail": f"{'查询成功' if success else '执行失败'}，{row_count} 行，耗时 {exec_ms}ms",
                        "duration_ms": exec_ms,
                    })

                    # Total time for cache hit
                    total_ms_cache = int((time.monotonic() - overall_start) * 1000)
                    total_time_ms += total_ms_cache
                else:
                    # Cache invalid, fall through to full pipeline
                    steps.append({
                        "type": "cache",
                        "label": "缓存检查",
                        "status": "done",
                        "detail": "缓存无效，进入 AI 查询管线",
                        "duration_ms": cache_ms,
                        "cached": False,
                    })
                    cache_type = None  # fall through

            # --- Full pipeline path ---
            if cache_type is None:
                pipe_start = time.monotonic()
                graph = build_graph()
                state = await graph.ainvoke({
                    "question": question,
                    "datasource_id": datasource_id,
                    "tenant_id": tenant_id,
                })
                elapsed_ms = int((time.monotonic() - pipe_start) * 1000)
                total_time_ms += elapsed_ms

                generated_sql = state.get("sql", "")
                success = state.get("success", False)
                rows = state.get("rows", [])
                columns = state.get("columns", [])
                row_count = state.get("row_count", 0)
                error = state.get("error")
                intent = state.get("intent", "")

                # Intent step
                if intent == "DataQuery":
                    steps.append({
                        "type": "intent",
                        "label": "意图识别: 数据查询",
                        "status": "done",
                        "detail": "识别为数据查询意图",
                    })
                else:
                    steps.append({
                        "type": "intent",
                        "label": "意图识别: 非数据查询",
                        "status": "failed",
                        "detail": error or "请提出数据查询相关的问题",
                    })
                    # Don't continue for non-DataQuery
                    steps.append({
                        "type": "complete",
                        "label": "结束",
                        "status": "done",
                        "detail": "非数据查询意图，跳过后续步骤",
                    })
                    cache_type = None  # not cached

                if intent == "DataQuery":
                    # Schema step
                    tables = state.get("selected_tables", [])
                    sel_cols = state.get("selected_columns", {})
                    if tables:
                        steps.append({
                            "type": "semantics",
                            "label": "Schema 选择",
                            "status": "done",
                            "detail": f"选择了 {len(tables)} 个表: {', '.join(tables)}",
                            "tables": tables,
                            "columns": sel_cols,
                        })
                    else:
                        steps.append({
                            "type": "semantics",
                            "label": "Schema 选择",
                            "status": "done",
                            "detail": "未找到相关表",
                        })

                    # SQL step
                    sql_step = {
                        "type": "sql",
                        "label": "SQL 生成",
                        "status": "done" if generated_sql else "failed",
                        "detail": "生成 SQL" if generated_sql else "未生成 SQL",
                        "sql": generated_sql,
                    }
                    if state.get("table_fixes") or state.get("column_fixes"):
                        v = {}
                        if state.get("table_fixes"):
                            v["table_fixes"] = state["table_fixes"]
                        if state.get("column_fixes"):
                            v["column_fixes"] = state["column_fixes"]
                        sql_step["validation"] = v
                    steps.append(sql_step)

                    # Execution step
                    sql_exec_ms = state.get("execution_time_ms")
                    steps.append({
                        "type": "data",
                        "label": "执行查询",
                        "status": "done" if success else "failed",
                        "detail": f"{'查询成功' if success else '执行失败'}，{row_count} 行" + (f"，耗时 {sql_exec_ms}ms" if sql_exec_ms else ""),
                        "duration_ms": sql_exec_ms,
                    })

                    # Chart step
                    chart = state.get("chart_type", "none")
                    if chart and chart != "none":
                        chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                        steps.append({
                            "type": "chart",
                            "label": "图表推断",
                            "status": "done",
                            "detail": f"推荐图表: {chart_labels.get(chart, chart)}",
                        })

                    # Slow query check
                    if elapsed_ms > settings.slow_query_threshold_ms:
                        steps.append({
                            "type": "complete",
                            "label": "慢查询",
                            "status": "done",
                            "detail": f"总耗时超过慢查询阈值",
                            "is_slow": True,
                        })

            total_ms = int((time.monotonic() - overall_start) * 1000)

            results.append({
                "question": question,
                "success": success,
                "generated_sql": generated_sql[:300] if generated_sql else "",
                "sql_match": bool(generated_sql and success),
                "row_count": row_count,
                "time_ms": total_ms,
                "error": error,
                "cached": cache_type is not None,
                "cache_type": cache_type,
                "source": "eval",
                "steps": steps,
            })

            # Log to audit table for monitoring
            try:
                from app.services.audit_service import log_action
                await log_action(
                    db, tenant_id, evaluator_user_id,
                    "EVAL_QUERY", "evaluation", datasource_id,
                    details=f"question={question[:200]} source=eval cached={cache_type}",
                    sql_text=generated_sql,
                    result_count=row_count,
                    execution_time_ms=total_ms,
                    error_message=error if not success else None,
                )
                await db.commit()
            except Exception:
                logger.exception("Failed to log eval query audit")

            if bool(generated_sql and success):
                sql_correct += 1

        except Exception as e:
            total_ms = int((time.monotonic() - overall_start) * 1000)
            results.append({
                "question": question,
                "success": False,
                "generated_sql": "",
                "sql_match": False,
                "row_count": 0,
                "time_ms": total_ms,
                "error": str(e),
                "cached": False,
                "cache_type": None,
                "source": "eval",
                "steps": steps,
            })

    total = len(dataset)
    return {
        "total": total,
        "sql_correct": sql_correct,
        "sql_accuracy": round(sql_correct / total, 2) if total > 0 else 0,
        "avg_time_ms": int(total_time_ms / total) if total > 0 else 0,
        "cache_hits": cache_hits,
        "results": results,
    }
