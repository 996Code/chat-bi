"""
查询管线执行器 — SSE 流式查询和后台查询的统一入口

=== 文件定位 ===
本文件是查询管线的**执行引擎**，负责将 AI 查询流程转化为**结构化事件流**。
它不使用 LangGraph 的 graph.ainvoke()，而是**直接调用各节点函数**，
逐步执行并产出 SSE 事件，供前端实时展示查询进度。

=== 为什么不用 graph.ainvoke()？===
LangGraph 的 graph.ainvoke() 是一个"黑盒"调用——传入初始 state，
等待最终 state 返回。这对于 SSE 流式输出不友好：
  1. 无法在执行过程中产出中间事件（intent、semantics、sql、data）
  2. 无法精确控制每个步骤的耗时统计
  3. 缓存命中时的逻辑与完整管线不同

因此，本文件采用"手撕管线"模式：按顺序调用各节点函数，
在每个步骤前后记录时间、产出事件，实现细粒度控制。

=== SSE 事件流 ===
前端通过 EventSource 连接 /query/stream 端点，接收以下事件：

事件序列：
  cache → intent → semantics → sql → data → chart → complete

事件格式：
  {"event": "intent", "data": {"intent": "DataQuery", "detail": "识别为数据查询意图", "duration_ms": 50}}

事件说明：
  - cache: 缓存检查结果（hit/miss，缓存类型）
  - intent: 意图识别结果（DataQuery / Other）
  - semantics: Schema 选择结果（选中的表和字段）
  - sql: SQL 生成结果（生成的 SQL 文本、验证信息）
  - data: SQL 执行结果（columns、rows、row_count）
  - chart: 图表类型推断（table/line/bar/pie/metric）
  - complete: 流程结束（success、is_slow、cached）
  - error: 异常错误（仅出错时）

=== 缓存策略 ===
本文件实现了两级缓存：

1. **精确缓存（Exact Cache）**：
   - key = hash(question + datasource_id + tenant_id)
   - 完全相同的问句直接返回缓存的 SQL 和结果
   - 命中时跳过 Schema 选择和 SQL 生成

2. **语义缓存（Semantic Cache）**：
   - key = embedding(question) 向量相似度匹配
   - 语义相近的问句（如"上个月销售额"和"上月销售额"）共享缓存
   - 使用 Redis 存储 embedding 向量

=== 自愈机制 ===
当 SQL 执行失败时，本文件会调用 self_heal_sql() 尝试修复：
  1. 提取错误码（MySQL error code）
  2. 调用 LLM 分析错误原因并生成修复后的 SQL
  3. 重新执行修复后的 SQL
  4. 最多尝试 1 次自愈

=== Python 异步生成器 ===
本文件使用 async generator（异步生成器）实现流式输出：

    async def execute_query_pipeline(...):
        yield {"event": "cache", "data": {...}}
        yield {"event": "intent", "data": {...}}
        # ...

调用方通过 async for 迭代获取事件：

    async for event in execute_query_pipeline(...):
        await event_source.send(event)

=== 与其他文件的关系 ===
- app/api/query.py — 调用本文件的 execute_query_pipeline()，SSE 返回事件流
- app/services/async_query_service.py — 调用本文件执行后台查询，存储中间状态到 Redis
- app/ai/graph.py — LangGraph 状态图定义（本文件不走 graph.ainvoke，但复用节点函数）
- app/ai/nodes/*.py — 各节点函数（classify_intent、schema_selection_node、generate_sql、execute_sql）
- app/ai/nodes/self_heal.py — SQL 自愈逻辑
- app/core/config.py — slow_query_threshold_ms 等配置

=== 关键 Python 概念 ===
- async generator: 使用 yield 的 async 函数，可被 async for 迭代
- time.monotonic(): 单调递增时钟，适合计算耗时（不受系统时间调整影响）
- dict unpacking: {**exec_result, "detail": ...} 合并字典
"""
import time
import json
from app.core.config import settings
from app.ai.chart_type import infer_chart_type


async def execute_query_pipeline(question: str, datasource_id: str, tenant_id: str, history: list[dict] | None = None):
    """
    执行完整查询管线，逐步产出 SSE 事件。

    这是本文件的核心函数，实现"手撕管线"模式——按顺序调用各节点函数，
    在每个步骤产出结构化事件，供前端实时展示查询进度。

    参数:
        question: 用户自然语言问题
        datasource_id: 目标数据源 ID
        tenant_id: 租户 ID（多租户隔离）
        history: 对话历史（多轮上下文），可选

    产出:
        dict — SSE 事件对象，格式：{"event": str, "data": dict}

    事件序列:
        cache → intent → semantics → sql → data → (chart) → complete

    工作流程:
        1. 检查精确缓存，命中则快速返回
        2. 检查语义缓存，命中则快速返回
        3. 缓存未命中，执行完整管线：
           a. 意图识别
           b. Schema 选择
           c. SQL 生成
           d. SQL 执行
           e. 失败时自愈
           f. 图表推断
        4. 返回 complete 事件

    Python 知识点:
        - async generator: 使用 yield 的 async 函数
        - yield: 暂停函数执行，返回一个值给调用方
        - return: 终止生成器，不再产出后续事件

    示例:
        async for event in execute_query_pipeline("各城市订单数", "ds-001", "tenant-001"):
            print(event["event"], event["data"])
        # 输出:
        # cache {"hit": false, "duration_ms": 5}
        # intent {"intent": "DataQuery", "detail": "识别为数据查询意图", "duration_ms": 50}
        # semantics {"detail": "选择了 1 个表: orders", "tables": ["orders"], ...}
        # sql {"sql": "SELECT city, COUNT(*) FROM orders GROUP BY city", ...}
        # data {"success": true, "row_count": 30, ...}
        # chart {"chart_type": "bar", "detail": "推荐图表: 柱状图"}
        # complete {"success": true, "is_slow": false, "cached": false}
    """
    start_time = time.monotonic()  # 记录管线开始时间（单调时钟，不受系统时间调整影响）
    final_success = False          # 最终是否成功
    final_sql = None               # 最终执行的 SQL（可能经过自愈修复）
    final_error = None             # 最终错误信息
    final_row_count = 0            # 返回行数
    final_rows = []                # 返回数据
    final_columns = []             # 返回列名
    sql_execution_ms = None        # SQL 执行耗时

    # ═══════════════════════════════════════════════════════════════
    # Step 0: 缓存检查 — 先检查精确缓存，再检查语义缓存
    # ═══════════════════════════════════════════════════════════════
    step_start = time.monotonic()
    from app.services.cache_service import cache_get, semantic_cache_get

    # ── 精确缓存检查 ──
    # key = hash(question + datasource_id + tenant_id)，完全匹配才命中
    cached = await cache_get(question, datasource_id, tenant_id)
    if cached:
        cache_duration = int((time.monotonic() - step_start) * 1000)
        yield {"event": "cache", "data": {"hit": True, "type": "exact", "duration_ms": cache_duration}}

        # ═══════════════════════════════════════════════════════════
        # 精确缓存命中分支 — 跳过 Schema 选择和 SQL 生成
        # ═══════════════════════════════════════════════════════════

        # Step 1: 意图识别（缓存命中也需要识别意图，用于判断是否数据查询）
        intent_start = time.monotonic()
        from app.ai.nodes.intent import classify_intent
        intent = await classify_intent(question)
        intent_duration = int((time.monotonic() - intent_start) * 1000)
        intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
        yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图（关键词匹配）", "duration_ms": intent_duration, "method": "cached"}}

        if intent != "DataQuery":
            # 非数据查询意图，友好提示并结束
            friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
            final_error = friendly_msg
            yield {"event": "complete", "data": {"success": False, "error": friendly_msg}}
            return  # 生成器终止

        # Step 2: 从缓存 SQL 中提取 Schema 信息（用于展示给前端）
        schema_start = time.monotonic()
        cached_sql = cached.get("sql")
        import re
        # 从 SQL 中提取表名（FROM 和 JOIN 子句）
        from_match = re.findall(r'FROM\s+(\w+)', cached_sql or '', re.IGNORECASE)
        join_match = re.findall(r'JOIN\s+(\w+)', cached_sql or '', re.IGNORECASE)
        selected_tables = list(dict.fromkeys(from_match + join_match))  # 去重保序
        cached_cols = cached.get("columns", [])
        # 按表分配字段（用于前端展示每个表用了哪些字段）
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

        # Step 3: SQL 安全校验（使用 SQLGlot AST 解析，确保只允许 SELECT）
        sql_start = time.monotonic()
        if cached_sql:
            import sqlglot
            try:
                parsed = sqlglot.parse_one(cached_sql, dialect="mysql")
                stmt_type = parsed.key.upper() if parsed.key else "UNKNOWN"
                if stmt_type != "SELECT":
                    # 非 SELECT 语句，拒绝执行（安全防护）
                    yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"缓存 SQL 非 SELECT 语句 ({stmt_type})，拒绝执行", "duration_ms": 0, "validation": {"rejected": True}}}
                    final_error = f"缓存 SQL 安全校验失败: 仅允许 SELECT 语句，检测到 {stmt_type}"
                    yield {"event": "complete", "data": {"success": False, "error": final_error}}
                    return
            except Exception as ast_err:
                # SQL 解析失败，仍然尝试执行（降级策略）
                yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"缓存 SQL 解析失败: {str(ast_err)}", "duration_ms": 0}}

            sql_duration = int((time.monotonic() - sql_start) * 1000)
            final_sql = cached_sql
            yield {"event": "sql", "data": {"sql": cached_sql, "detail": f"使用缓存 SQL（精确缓存命中，{cache_duration}ms）", "duration_ms": sql_duration, "source": "cached"}}

            # Step 4: 执行缓存的 SQL
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

            # Step 5: 自愈（如果缓存的 SQL 执行失败）
            if not final_success:
                from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
                error_code = extract_error_code(final_error or "")
                schema_ctx = f"Tables: {', '.join(selected_tables)}" if selected_tables else ""
                heal_result = await self_heal_sql(
                    question=question, sql=cached_sql, error=final_error or "",
                    datasource_id=datasource_id, schema_context=schema_ctx, dialect="mysql",
                )
                if heal_result.get("success"):
                    # 自愈成功，重新执行修复后的 SQL
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
            # 缓存中没有 SQL，降级到完整管线（理论上不应该发生）
            pass

        # Step 6: 图表推断
        if final_rows and final_columns:
            chart_type = infer_chart_type(final_columns, final_rows)
            chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
            chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（缓存数据）"
            yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

        # 管线结束，产出 complete 事件
        total_ms = int((time.monotonic() - start_time) * 1000)
        is_slow = total_ms > settings.slow_query_threshold_ms
        yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": True, "cache_type": "exact"}}
        return  # 精确缓存命中分支结束

    # ═══════════════════════════════════════════════════════════════
    # 语义缓存检查 — 向量相似度匹配，语义相近的问句共享缓存
    # ═══════════════════════════════════════════════════════════════
    sem_cached = await semantic_cache_get(question, datasource_id, tenant_id)
    if sem_cached:
        cache_duration = int((time.monotonic() - step_start) * 1000)
        yield {"event": "cache", "data": {"hit": True, "type": "semantic", "duration_ms": cache_duration}}

        sem_sql = sem_cached.get("sql")
        if not sem_sql:
            pass  # 语义缓存中没有 SQL，降级到完整管线
        else:
            # ═══════════════════════════════════════════════════════════
            # 语义缓存命中分支 — 与精确缓存类似，但标记为 semantic
            # ═══════════════════════════════════════════════════════════

            # Step 1: 意图识别
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

            # Step 2: 提取 Schema
            import re
            schema_start = time.monotonic()
            sem_tables = sem_cached.get("tables") or []
            if not sem_tables:
                # 缓存中没有表信息，从 SQL 中提取
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

            # Step 3: SQL 安全校验
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
                pass  # 解析失败仍然尝试执行
            sql_duration = int((time.monotonic() - sql_start) * 1000)
            final_sql = sem_sql
            yield {"event": "sql", "data": {"sql": sem_sql, "detail": f"使用缓存 SQL（语义缓存命中，{cache_duration}ms）", "duration_ms": sql_duration, "source": "semantic_cached"}}

            # Step 4: 执行
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

            # Step 5: 自愈
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

            # Step 6: 图表推断
            if final_rows and final_columns:
                chart_type = infer_chart_type(final_columns, final_rows)
                chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
                chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}（语义缓存数据）"
                yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

            total_ms = int((time.monotonic() - start_time) * 1000)
            is_slow = total_ms > settings.slow_query_threshold_ms
            yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": True, "cache_type": "semantic"}}
            return  # 语义缓存命中分支结束

    # ═══════════════════════════════════════════════════════════════
    # 缓存未命中 — 执行完整查询管线
    # ═══════════════════════════════════════════════════════════════
    cache_duration = int((time.monotonic() - step_start) * 1000)
    yield {"event": "cache", "data": {"hit": False, "duration_ms": cache_duration}}

    try:
        # ── Step 1: 意图识别 ──
        # 调用 intent.py 的 classify_intent()，判断问题是数据查询还是其他
        step_start = time.monotonic()
        from app.ai.nodes.intent import classify_intent
        intent = await classify_intent(question)
        intent_duration = int((time.monotonic() - step_start) * 1000)
        intent_label = "数据查询" if intent == "DataQuery" else "非数据查询"
        yield {"event": "intent", "data": {"intent": intent, "detail": f"识别为{intent_label}意图", "duration_ms": intent_duration}}

        if intent != "DataQuery":
            # 非数据查询意图，友好提示并结束
            friendly_msg = "我是数据查询助手，可以帮你查询和分析数据。请试试这样的问题：\n• 各城市的订单数量\n• 上个月的销售额是多少\n• VIP 等级的用户分布"
            final_error = friendly_msg
            yield {"event": "complete", "data": {"success": False, "error": friendly_msg}}
            return

        # ── Step 2: Schema 选择 ──
        # 调用 schema_selection.py，两步 LLM：先选表，再选列
        step_start = time.monotonic()
        from app.ai.nodes.schema_selection import schema_selection_node
        # 构造初始 state（与 LangGraph 的 QueryState 结构一致）
        state = {
            "question": question,
            "datasource_id": datasource_id,
            "tenant_id": tenant_id,
        }
        schema_result = await schema_selection_node(state)
        schema_context = schema_result.get("schema_context", "")  # DDL 文本
        raw_metadata = schema_result.get("raw_metadata", "")      # JSON 元数据
        selected_tables = schema_result.get("selected_tables", [])
        selected_columns = schema_result.get("selected_columns", {})
        schema_duration = int((time.monotonic() - step_start) * 1000)

        schema_detail = f"选择了 {len(selected_tables)} 个表: {', '.join(selected_tables)}" if selected_tables else "未找到相关表"
        yield {"event": "semantics", "data": {"intent": "schema_selected", "detail": schema_detail, "duration_ms": schema_duration, "tables": selected_tables, "columns": selected_columns}}

        # ── Step 3: SQL 生成 ──
        # 调用 generation.py，三次尝试降级策略
        step_start = time.monotonic()
        from app.ai.nodes.generation import generate_sql
        # 注意：generation.py 接收 history 参数用于多轮对话上下文
        gen_result = await generate_sql(question, schema_context, raw_metadata=raw_metadata, history=history)
        gen_duration = int((time.monotonic() - step_start) * 1000)

        # 兼容两种返回格式：dict 或 str
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
        # 构造验证信息（表名/列名修正记录）
        validation = {}
        if table_fixes:
            validation["table_fixes"] = table_fixes
        if column_fixes:
            validation["column_fixes"] = column_fixes
        yield {"event": "sql", "data": {"sql": sql, "detail": sql_detail, "duration_ms": gen_duration, "attempt": gen_attempt, "validation": validation if validation else None}}

        if not sql:
            # SQL 生成失败，结束管线
            final_error = "无法生成 SQL"
            yield {"event": "complete", "data": {"success": False, "error": final_error}}
            return

        # ── Step 4: SQL 执行 ──
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

        # ── Step 5: 自愈（失败时）──
        # 如果 SQL 执行失败且有 schema_context，尝试让 LLM 修复 SQL
        if not final_success and schema_context:
            from app.ai.nodes.self_heal import self_heal_sql, extract_error_code
            error_code = extract_error_code(final_error or "")
            heal_result = await self_heal_sql(
                question=question, sql=sql, error=final_error or "",
                datasource_id=datasource_id, schema_context=schema_context, dialect="mysql",
            )
            if heal_result.get("success"):
                # 自愈成功，重新执行修复后的 SQL
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

        # ── Step 6: 图表推断 ──
        # 根据返回数据的特征，推断最合适的图表类型
        if exec_result.get("rows") and exec_result.get("columns"):
            chart_type = infer_chart_type(exec_result["columns"], exec_result["rows"])
            chart_labels = {"table": "表格", "line": "折线图", "bar": "柱状图", "pie": "饼图", "metric": "指标卡"}
            chart_detail = f"推荐图表: {chart_labels.get(chart_type, chart_type)}"
            yield {"event": "chart", "data": {"chart_type": chart_type, "detail": chart_detail}}

        # ── 管线结束 ──
        # 检查是否为慢查询，产出 complete 事件
        total_ms = int((time.monotonic() - start_time) * 1000)
        is_slow = total_ms > settings.slow_query_threshold_ms
        yield {"event": "complete", "data": {"success": final_success, "is_slow": is_slow, "cached": False}}

    except Exception as e:
        # 异常处理：记录日志，产出 error 事件
        import logging
        logging.getLogger(__name__).exception("Pipeline error")
        final_error = str(e)
        yield {"event": "error", "data": {"error": str(e)}}
