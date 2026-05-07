"""Shared query pipeline executor — yields structured step events for both SSE and async queries."""
import time
import json
from app.core.config import settings
from app.ai.chart_type import infer_chart_type


async def execute_query_pipeline(question: str, datasource_id: str, tenant_id: str, history: list[dict] | None = None):
    """执行完整查询管线，逐步产出事件。

    产出 dict: {"event": str, "data": dict}
    事件序列: cache → intent → semantics → sql → data → (chart) → complete
    """
    start_time = time.monotonic()
    final_success = False
    final_sql = None
    final_error = None
    final_row_count = 0
    final_rows = []
    final_columns = []
    sql_execution_ms = None

    # Step 0: Cache check
    step_start = time.monotonic()
    from app.services.cache_service import cache_get, semantic_cache_get

    cached = await cache_get(question, datasource_id, tenant_id)
    if cached:
        cache_duration = int((time.monotonic() - step_start) * 1000)
        yield {"event": "cache", "data": {"hit": True, "type": "exact", "duration_ms": cache_duration}}
        # Run full pipeline with cached SQL
        intent_start = time.monotonic()
        from app.ai.nodes.intent import classify_intent
        intent = await classify_intent(question)
        intent_duration = int((time.monotonic() - intent_start) * 1000)
        intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
        yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图（关键词匹配）", "duration_ms": intent_duration, "method": "cached"}}

        if intent != "DataQuery":
            friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
            final_error = friendly_msg
            yield {"event": "complete", "data": {"success": False, "error": friendly_msg}}
            return

        # Schema from cached SQL
        schema_start = time.monotonic()
        cached_sql = cached.get("sql")
        import re
        from_match = re.findall(r'FROM\s+(\w+)', cached_sql or '', re.IGNORECASE)
        join_match = re.findall(r'JOIN\s+(\w+)', cached_sql or '', re.IGNORECASE)
        selected_tables = list(dict.fromkeys(from_match + join_match))
        cached_cols = cached.get("columns", [])
        if selected_tables:
            per_table = max(1, len(cached_cols) // len(selected_tables))
            selected_columns = {}
            for i, t in enumerate(selected_tables):
                start_idx = i * per_table
                selected_columns[t] = cached_cols[start_idx:start_idx + per_table]
        else:
            selected_columns = {"columns": cached_cols[:15]}
        schema_duration = int((time.monotonic() - schema_start) * 1000)
        table_detail = f"选择了 {len(selected_tables)} 个表: {', '.join(selected_tables)}" if selected_tables else f"使用缓存 Schema（{len(cached_cols)} 个字段）"
        yield {"event": "semantics", "data": {"detail": table_detail, "tables": selected_tables, "columns": selected_columns, "duration_ms": schema_duration, "source": "cached"}}

        # SQL validation
        sql_start = time.monotonic()
        if cached_sql:
            import sqlglot
            try:
                parsed = sqlglot.parse_one(cached_sql, dialect="mysql")
                stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
                if stmt_type != "SELECT":
                    yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"缓存 SQL 非 SELECT 语句 ({stmt_type})，拒绝执行", "duration_ms": 0, "validation": {"rejected": True}}}
                    final_error = f"缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
                    yield {"event": "complete", "data": {"success": False, "error": final_error}}
                    return
            except Exception as ast_err:
                yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"缓存 SQL 解析失败: {str(ast_err)}", "duration_ms": 0}}

            sql_duration = int((time.monotonic() - sql_start) * 1000)
            final_sql = cached_sql
            yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"使用缓存 SQL（精确缓存命中，{cache_duration}ms）", "duration_ms": sql_duration, "source": "cached"}}

            # Execute cached SQL
            exec_start = time.monotonic()
            from app.ai.nodes.execution import execute_sql
            exec_result = await execute_sql(cached_sql, datasource_id, tenant_id=tenant_id)
            exec_duration = int((time.monotonic() - exec_start) * 1000)
            sql_execution_ms = exec_duration
            final_success = exec_result.get("success", False)
            final_row_count = exec_result.get("row_count", 0)
            final_rows = exec_result.get("rows", [])
            final_columns = exec_result.get("columns", [])
            final_error = exec_result.get("error")
            exec_detail = f"查询成功，返回 {final_row_count} 行（缓存 SQL 执行，{exec_duration}ms）" if final_success else f"执行失败: {final_error}"
            yield {"event": "data", "data": {**exec_result, "detail": exec_detail, "duration_ms": exec_duration}}

            # Self-heal if cached SQL failed
            if not final_success:
                from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
                error_code = extract_error_code(final_error or "")
                schema_ctx = f"Tables: {', '.join(selected_tables)}" if selected_tables else ""
                heal_result = await self_heal_sql(
                    question=question, sql=cached_sql, error=final_error or "",
                    datasource_id=datasource_id, schema_context=schema_ctx, dialect="mysql",
                )
                if heal_result.get("success"):
                    final_sql = heal_result.get("sql", cached_sql)
                    heal_exec_start = time.monotonic()
                    exec_result2 = await execute_sql(final_sql, datasource_id, tenant_id=tenant_id)
                    sql_execution_ms = int((time.monotonic() - heal_exec_start) * 1000)
                    final_success = exec_result2.get("success", False)
                    final_row_count = exec_result2.get("row_count", 0)
                    final_rows = exec_result2.get("rows", [])
                    final_columns = exec_result2.get("columns", [])
                    final_error = exec_result2.get("error")
                    yield {"event": "sql", "data": {"sql": final_sql, "detail": f"自愈成功，修正后 SQL: {final_sql[:60]}...", "error_code": error_code, "retry": 1}}
                    exec_detail = f"查询成功，返回 {final_row_count} 行（自愈执行，{sql_execution_ms}ms）" if final_success else f"执行失败: {final_error}"
                    yield {"event": "data", "data": {**exec_result2, "detail": exec_detail, "duration_ms": sql_execution_ms}}
        else:
            # No cached SQL, fall through to normal pipeline
            pass

        # Chart
        if final_rows and final_columns:
            chart_type = infer_chart_type(final_columns, final_rows)
            chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
            chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（缓存数据）"
            yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

        total_ms = int((time.monotonic() - start_time) * 1000)
        is_slow = total_ms > settings.slow_query_threshold_ms
        yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": True, "cache_type": "exact"}}
        return

    # Semantic cache check
    sem_cached = await semantic_cache_get(question, datasource_id, tenant_id)
    if sem_cached:
        cache_duration = int((time.monotonic() - step_start) * 1000)
        yield {"event": "cache", "data": {"hit": True, "type": "semantic", "duration_ms": cache_duration}}

        sem_sql = sem_cached.get("sql")
        if not sem_sql:
            pass  # Fall through to normal pipeline
        else:
            # Intent
            intent_start = time.monotonic()
            from app.ai.nodes.intent import classify_intent
            intent = await classify_intent(question)
            intent_duration = int((time.monotonic() - intent_start) * 1000)
            intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
            yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图（语义缓存命中）", "duration_ms": intent_duration}}

            if intent != "DataQuery":
                friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
                final_error = friendly_msg
                yield {"event": "complete", "data": {"success": False, "error": friendly_msg}}
                return

            # Schema
            import re
            schema_start = time.monotonic()
            sem_tables = sem_cached.get("tables") or []
            if not sem_tables:
                sem_tables = re.findall(r'FROM\s+(\w+)', sem_sql or '', re.IGNORECASE)
                sem_tables = list(dict.fromkeys(sem_tables))
            sem_cols_raw = sem_cached.get("columns", [])
            if sem_tables:
                per_table = max(1, len(sem_cols_raw) // len(sem_tables))
                sem_cols = {t: sem_cols_raw[i*per_table:(i+1)*per_table] for i, t in enumerate(sem_tables)}
            else:
                sem_cols = {"columns": sem_cols_raw[:15]}
            schema_duration = int((time.monotonic() - schema_start) * 1000)
            table_detail = f"选择了 {len(sem_tables)} 个表: {', '.join(sem_tables)}" if sem_tables else f"语义缓存 Schema（{len(sem_cols_raw)} 个字段）"
            yield {"event": "semantics", "data": {"detail": table_detail, "tables": sem_tables, "columns": sem_cols, "duration_ms": schema_duration, "source": "semantic_cached"}}

            # SQL validation
            sql_start = time.monotonic()
            import sqlglot
            try:
                parsed = sqlglot.parse_one(sem_sql, dialect="mysql")
                stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
                if stmt_type != "SELECT":
                    final_error = f"语义缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
                    yield {"event": "complete", "data": {"success": False, "error": final_error}}
                    return
            except Exception:
                pass
            sql_duration = int((time.monotonic() - sql_start) * 1000)
            final_sql = sem_sql
            yield {"event": "sql", "data": {"sql": sem_sql, "detail": f"使用缓存 SQL（语义缓存命中，{cache_duration}ms）", "duration_ms": sql_duration, "source": "semantic_cached"}}

            # Execute
            exec_start = time.monotonic()
            from app.ai.nodes.execution import execute_sql
            exec_result = await execute_sql(sem_sql, datasource_id, tenant_id=tenant_id)
            exec_duration = int((time.monotonic() - exec_start) * 1000)
            sql_execution_ms = exec_duration
            final_success = exec_result.get("success", False)
            final_row_count = exec_result.get("row_count", 0)
            final_rows = exec_result.get("rows", [])
            final_columns = exec_result.get("columns", [])
            final_error = exec_result.get("error")
            exec_detail = f"查询成功，返回 {final_row_count} 行（语义缓存 SQL 执行，{exec_duration}ms）" if final_success else f"执行失败: {final_error}"
            yield {"event": "data", "data": {**exec_result, "detail": exec_detail, "duration_ms": exec_duration}}

            # Self-heal
            if not final_success:
                from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
                error_code = extract_error_code(final_error or "")
                heal_result = await self_heal_sql(
                    question=question, sql=sem_sql, error=final_error or "",
                    datasource_id=datasource_id, schema_context="", dialect="mysql",
                )
                if heal_result.get("success"):
                    final_sql = heal_result.get("sql", sem_sql)
                    heal_exec_start = time.monotonic()
                    exec_result2 = await execute_sql(final_sql, datasource_id, tenant_id=tenant_id)
                    sql_execution_ms = int((time.monotonic() - heal_exec_start) * 1000)
                    final_success = exec_result2.get("success", False)
                    final_row_count = exec_result2.get("row_count", 0)
                    final_rows = exec_result2.get("rows", [])
                    final_columns = exec_result2.get("columns", [])
                    final_error = exec_result2.get("error")
                    yield {"event": "sql", "data": {"sql": final_sql, "detail": f"自愈成功，修正后 SQL: {final_sql[:60]}...", "error_code": error_code, "retry": 1}}
                    exec_detail = f"查询成功，返回 {final_row_count} 行（自愈执行，{sql_execution_ms}ms）" if final_success else f"执行失败: {final_error}"
                    yield {"event": "data", "data": {**exec_result2, "detail": exec_detail, "duration_ms": sql_execution_ms}}

            # Chart
            if final_rows and final_columns:
                chart_type = infer_chart_type(final_columns, final_rows)
                chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（语义缓存数据）"
                yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

            total_ms = int((time.monotonic() - start_time) * 1000)
            is_slow = total_ms > settings.slow_query_threshold_ms
            yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": True, "cache_type": "semantic"}}
            return

    # Cache miss — normal pipeline
    cache_duration = int((time.monotonic() - step_start) * 1000)
    yield {"event": "cache", "data": {"hit": False, "duration_ms": cache_duration}}

    try:
        # Step 1: Intent
        step_start = time.monotonic()
        from app.ai.nodes.intent import classify_intent
        intent = await classify_intent(question)
        intent_duration = int((time.monotonic() - step_start) * 1000)
        intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
        yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图", "duration_ms": intent_duration}}

        if intent != "DataQuery":
            friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
            final_error = friendly_msg
            yield {"event": "complete", "data": {"success": False, "error": friendly_msg}}
            return

        # Step 2: Schema selection
        step_start = time.monotonic()
        from app.ai.nodes.schema_selection import schema_selection_node
        state = {
            "question": question,
            "datasource_id": datasource_id,
            "tenant_id": tenant_id,
        }
        schema_result = await schema_selection_node(state)
        schema_context = schema_result.get("schema_context", "")
        raw_metadata = schema_result.get("raw_metadata", "")
        selected_tables = schema_result.get("selected_tables", [])
        selected_columns = schema_result.get("selected_columns", {})
        schema_duration = int((time.monotonic() - step_start) * 1000)

        schema_detail = f"选择了 {len(selected_tables)} 个表: {', '.join(selected_tables)}" if selected_tables else "未找到相关表"
        yield {"event": "semantics", "data": {"intent": "schema_selected", "detail": schema_detail, "duration_ms": schema_duration, "tables": selected_tables, "columns": selected_columns}}

        # Step 3: SQL generation
        step_start = time.monotonic()
        from app.ai.nodes.generation import generate_sql
        gen_result = await generate_sql(question, schema_context, raw_metadata=raw_metadata, history=history)
        gen_duration = int((time.monotonic() - step_start) * 1000)

        if isinstance(gen_result, dict):
            sql = gen_result.get("sql", "")
            gen_attempt = gen_result.get("attempt", 1)
            table_fixes = gen_result.get("table_fixes", [])
            column_fixes = gen_result.get("column_fixes", [])
        else:
            sql = gen_result or ""
            gen_attempt = 1
            table_fixes = []
            column_fixes = []

        final_sql = sql
        sql_detail = f"生成 SQL: {sql[:80]}..." if sql and len(sql) > 80 else f"生成 SQL: {sql or '空'}"
        validation = {}
        if table_fixes:
            validation["table_fixes"] = table_fixes
        if column_fixes:
            validation["column_fixes"] = column_fixes
        yield {"event": "sql", "data": {"sql": sql, "detail": sql_detail, "duration_ms": gen_duration, "attempt": gen_attempt, "validation": validation if validation else None}}

        if not sql:
            final_error = "无法生成 SQL"
            yield {"event": "complete", "data": {"success": False, "error": final_error}}
            return

        # Step 4: Execute
        step_start = time.monotonic()
        from app.ai.nodes.execution import execute_sql
        exec_result = await execute_sql(sql, datasource_id, tenant_id=tenant_id)
        exec_duration = int((time.monotonic() - step_start) * 1000)
        sql_execution_ms = exec_duration
        final_success = exec_result.get("success", False)
        final_row_count = exec_result.get("row_count", 0)
        final_rows = exec_result.get("rows", [])
        final_columns = exec_result.get("columns", [])
        final_error = exec_result.get("error")
        exec_detail = f"查询成功，返回 {final_row_count} 行" if final_success else f"执行失败: {final_error}"
        yield {"event": "data", "data": {**exec_result, "detail": exec_detail, "duration_ms": exec_duration}}

        # Step 5: Self-heal on failure
        if not final_success and schema_context:
            from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
            error_code = extract_error_code(final_error or "")
            heal_result = await self_heal_sql(
                question=question, sql=sql, error=final_error or "",
                datasource_id=datasource_id, schema_context=schema_context, dialect="mysql",
            )
            if heal_result.get("success"):
                final_sql = heal_result.get("sql", final_sql)
                exec_start_heal = time.monotonic()
                exec_result = await execute_sql(final_sql, datasource_id, tenant_id=tenant_id)
                sql_execution_ms = int((time.monotonic() - exec_start_heal) * 1000)
                final_success = exec_result.get("success", False)
                final_row_count = exec_result.get("row_count", 0)
                final_error = exec_result.get("error")
                heal_detail = f"自愈成功，修正后 SQL: {final_sql[:60]}..." if len(final_sql) > 60 else f"自愈成功，修正后 SQL: {final_sql}"
                yield {"event": "sql", "data": {"sql": final_sql, "detail": heal_detail, "error_code": error_code, "retry": 1}}
                exec_detail = f"查询成功，返回 {final_row_count} 行" if final_success else f"执行失败: {final_error}"
                yield {"event": "data", "data": {**exec_result, "detail": exec_detail}}

        # Step 6: Chart type
        if exec_result.get("rows") and exec_result.get("columns"):
            chart_type = infer_chart_type(exec_result["columns"], exec_result["rows"])
            chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
            chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}"
            yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

        # Slow query check
        total_ms = int((time.monotonic() - start_time) * 1000)
        is_slow = total_ms > settings.slow_query_threshold_ms
        yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": False}}

    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Pipeline error")
        final_error = str(e)
        yield {"event": "error", "data": {"error": str(e)}}
