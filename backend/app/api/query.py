"""
查询 API —— 同步查询、SSE 流式查询、SQL 解释、原始 SQL 执行、CSV 导出、异步查询

本文件是 ChatBI 最核心的 API 模块，负责将用户的自然语言问题转化为 SQL 并返回查询结果。

核心概念
--------
1. **AI 查询管道 (Pipeline)**：用户提问 → 意图识别 → Schema 选择 → SQL 生成 → 执行 → 自愈 → 图表推断
   - 同步接口 `create_query` 使用 LangGraph 图一次性执行完整管道
   - 流式接口 `stream_query` 通过 `pipeline_executor` 逐步推送各阶段结果
   - 异步接口 `submit_async_query` 在后台运行管道，前端轮询获取进度

2. **缓存策略**：精确缓存 (exact) → 语义缓存 (semantic) → 完整管道
   - 精确缓存：问题 + 数据源完全匹配，直接返回历史结果
   - 语义缓存：问题语义相似（如"各城市销量"≈"按城市统计销量"），复用 SQL 重新执行

3. **审计与追踪**：每次查询都会记录审计日志 (audit_service) 和查询历史 (SavedQuery)

与其他文件的关系
----------------
- `app/ai/graph.py`：构建 LangGraph 状态图，定义 AI 管道的节点和边
- `app/services/pipeline_executor.py`：流式管道执行器，逐步 yield 事件
- `app/services/cache_service.py`：精确缓存 + 语义缓存的读写
- `app/services/audit_service.py`：审计日志记录（谁在什么时候做了什么）
- `app/services/analytics_service.py`：事件追踪（统计查询成功/失败率等）
- `app/schemas/query.py`：请求/响应的 Pydantic 数据模型
- `app/api/_helpers.py`：共享工具函数 api_error()、iso_format()
"""
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

# APIRouter 将本模块的所有路由注册到 /query 前缀下
# tags=["查询"] 用于 Swagger UI 分组显示
router = APIRouter(prefix="/query", tags=["查询"])


from app.api._helpers import api_error


async def _check_datasource(datasource_id: str, tenant_id: str, db: AsyncSession) -> DataSource:
    """
    校验数据源：是否存在、是否属于当前租户、是否已启用。

    参数
    ----
    datasource_id : str
        数据源 UUID
    tenant_id : str
        当前用户的租户 ID，用于多租户隔离（不同租户看不到彼此的数据源）
    db : AsyncSession
        SQLAlchemy 异步数据库会话，由 FastAPI 的 Depends(get_db) 注入

    返回
    ----
    DataSource : 数据源 ORM 对象，校验通过后返回，供后续使用

    抛出
    ----
    HTTPException(404) : 数据源不存在或不属于当前租户
    HTTPException(400) : 数据源已被管理员禁用

    Python 提示
    -----------
    - `scalar_one_or_none()` 是 SQLAlchemy 的便捷方法：恰好一行则返回对象，零行返回 None，多行抛异常
    - 这里用 `DataSource.tenant_id == tenant_id` 实现租户隔离，是 SaaS 多租户的基本模式
    """
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
            detail=api_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )
    if not ds.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("DATASOURCE_INACTIVE", "数据源已禁用，请联系管理员启用"),
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
    """
    自动保存查询历史记录到 SavedQuery 表。

    每次查询执行后调用，无论成功或失败都保存，用于：
    - 用户查看自己的查询历史
    - 管理员审计和统计
    - 缓存预热（热门查询自动缓存）

    参数
    ----
    sql : str | None
        生成的 SQL，失败时可能为 None
        Python 3.10+ 语法 `str | None` 等价于 `Optional[str]`
    success : bool
        查询是否成功执行
    row_count : int
        返回的行数，0 表示无数据或执行失败
    execution_time_ms : int | None
        总耗时（毫秒），包含 AI 推理 + SQL 执行
    error : str | None
        失败时的错误信息
    chart_type : str | None
        推断的图表类型（table/line/bar/pie/metric）

    注意
    ----
    - 此函数只做 `db.add()`，不调用 `db.commit()`
    - commit 由调用方统一执行，这是 SQLAlchemy 的常见模式：多个操作共享一个事务
    - name=None 表示这是自动保存的历史，不是用户手动保存的查询
    """
    sq = SavedQuery(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        datasource_id=datasource_id,
        name=None,  # name=None 表示自动保存，非用户手动命名
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
    """
    同步查询接口 —— 最核心的 API，将自然语言问题转为 SQL 并返回结果。

    处理流程（按优先级依次尝试）
    ----------------------------
    1. **精确缓存**：问题 + 数据源完全匹配 → 直接返回缓存结果
    2. **语义缓存**：问题语义相似 → 复用 SQL 重新执行
    3. **轻量模型路由**：简单问题（如"有多少用户"）用小模型快速处理
    4. **完整 AI 管道**：LangGraph 状态图执行全部节点

    参数
    ----
    data : QueryRequest
        Pydantic 模型，包含 question（自然语言问题）、datasource_id、history（对话历史）
    user : dict
        由 get_current_user 依赖注入，包含 tenant_id、user_id、role 等鉴权信息
    db : AsyncSession
        异步数据库会话

    返回
    ----
    QueryResponse : 包含 success、sql、columns、rows、chart_type 等字段

    Python 提示
    -----------
    - `Depends(get_current_user)` 是 FastAPI 的依赖注入：请求到达时自动执行
      get_current_user 从 JWT token 解析用户信息，失败返回 401
    - `time.monotonic()` 比 `time.time()` 更适合计算耗时，因为它不受系统时钟调整影响
    - `await db.commit()` 提交当前事务中所有待处理的数据库操作
    """
    start = time.monotonic()  # 记录请求开始时间，用于计算总耗时
    tenant_id = user["tenant_id"]

    ds = await _check_datasource(data.datasource_id, tenant_id, db)

    # ---- 第 1 步：精确缓存 ----
    # 精确匹配 = 问题文本 + 数据源 ID + 租户 ID 完全一致
    # 优点：零延迟，直接返回；缺点：换个说法就命中不了
    cached = await cache_get(data.question, data.datasource_id, tenant_id)
    if cached:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        # 延迟导入：只在缓存命中时才加载 analytics_service，减少启动时间
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

    # ---- 第 2 步：语义缓存 ----
    # 用向量相似度匹配语义相近的问题，复用其 SQL 重新执行
    # 例如 "各城市销量" 和 "按城市统计销量" 语义相似，可复用 SQL
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

    # ---- 第 3 步：轻量模型路由 ----
    # 当配置了 llm_simple_model 时，简单问题（复杂度分数 ≤ 2）走小模型
    # 小模型更快更便宜，但只能处理简单查询（如单表聚合）
    if settings.llm_simple_model:
        from app.services.query_complexity import estimate_query_complexity
        complexity = estimate_query_complexity(data.question)
        if complexity["score"] <= 2:
            logger.info("Simple query path (score=%d): %s", complexity["score"], data.question[:80])
            from app.services.simple_query_executor import execute_simple_query
            simple_result = await execute_simple_query(data.question, data.datasource_id, tenant_id)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            # 根据返回数据推断图表类型
            chart_type = "table"
            if simple_result.get("rows") and simple_result.get("columns"):
                chart_type = infer_chart_type(simple_result["columns"], simple_result["rows"])

            # 审计 + 事件追踪
            from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
            await track_event(db, tenant_id, user["user_id"], EVENT_QUERY_EXECUTE, {
                "question": data.question,
                "simple_model": True,
                "complexity_score": complexity["score"],
            })
            event_name = EVENT_QUERY_SUCCESS if simple_result.get("success") else EVENT_QUERY_ERROR
            await track_event(db, tenant_id, user["user_id"], event_name, {
                "question": data.question,
                "simple_model": True,
            })
            await _auto_save_history(
                db, tenant_id, user["user_id"], data.datasource_id,
                data.question, simple_result.get("sql"),
                success=simple_result.get("success", False),
                row_count=simple_result.get("row_count", 0),
                execution_time_ms=elapsed_ms,
                error=simple_result.get("error"),
                chart_type=chart_type,
            )
            await db.commit()

            response = QueryResponse(
                success=simple_result.get("success", False),
                intent="DataQuery",
                sql=simple_result.get("sql"),
                columns=simple_result.get("columns", []),
                rows=simple_result.get("rows", []),
                row_count=simple_result.get("row_count", 0),
                error=simple_result.get("error"),
                execution_time_ms=simple_result.get("execution_time_ms") or elapsed_ms,
                chart_type=chart_type,
            )

            # 成功且有数据时写入缓存，下次相同问题可直接命中
            if simple_result.get("success") and simple_result.get("rows"):
                await cache_set(data.question, data.datasource_id, {
                    "success": True,
                    "intent": "DataQuery",
                    "sql": simple_result.get("sql"),
                    "columns": simple_result.get("columns", []),
                    "rows": simple_result.get("rows", []),
                    "row_count": simple_result.get("row_count", 0),
                    "chart_type": chart_type,
                }, tenant_id=tenant_id)

            return response

    # ---- 第 4 步：完整 AI 管道 ----
    # 构建并执行 LangGraph 状态图：意图识别 → Schema 选择 → SQL 生成 → 执行 → 自愈
    try:
        graph = build_graph()  # 构建 LangGraph 状态图（节点 + 边）
        initial_state = {
            "question": data.question,
            "datasource_id": data.datasource_id,
            "tenant_id": tenant_id,
            "conversation_history": data.history or [],  # 多轮对话历史
        }

        # asyncio.wait_for 为管道执行设置超时，防止 LLM 无响应时请求挂起
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),  # ainvoke = 异步调用 LangGraph 图
            timeout=settings.query_pipeline_timeout,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # 根据返回的列和行数据推断最适合的图表类型
        chart_type = "none"
        columns = final_state.get("columns", [])
        rows = final_state.get("rows", [])
        if rows and columns:
            chart_type = infer_chart_type(columns, rows)

        # 对敏感字段（如手机号、身份证）做脱敏处理
        from app.services.data_masking import mask_sensitive_data
        columns, rows = mask_sensitive_data(columns, rows)

        # 查找最近 2 分钟内的对话记录，用于审计日志关联
        # 这样可以在审计日志中追踪"这次查询属于哪次对话"
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
            pass  # 对话关联是可选的，失败不影响主流程

        # ---- 审计日志 + 事件追踪 ----
        # 审计日志记录"谁做了什么"，用于合规和问题排查
        # 事件追踪记录统计指标，用于分析查询成功率等
        from app.services.audit_service import log_action
        from app.services.analytics_service import track_event, EVENT_QUERY_EXECUTE, EVENT_QUERY_SUCCESS, EVENT_QUERY_ERROR
        sql_exec_ms = final_state.get("execution_time_ms")  # 纯 SQL 执行耗时（不含 AI 推理）
        await log_action(
            db, tenant_id, user["user_id"],
            "QUERY_EXECUTE", "query", data.datasource_id,
            details=f"question={data.question[:200]} intent={final_state.get('intent')}",
            sql_text=final_state.get("sql"),
            result_count=final_state.get("row_count", 0),
            execution_time_ms=elapsed_ms,  # 总耗时 = AI 推理 + SQL 执行
            sql_execution_time_ms=sql_exec_ms,  # 纯 SQL 执行耗时
            error_message=final_state.get("error") if not final_state.get("success") else None,
            conversation_id=conv_id,
        )
        event_name = EVENT_QUERY_SUCCESS if final_state.get("success") else EVENT_QUERY_ERROR
        await track_event(db, tenant_id, user["user_id"], event_name, {
            "question": data.question,
            "success": final_state.get("success"),
        })

        # 自动保存查询历史
        await _auto_save_history(
            db, tenant_id, user["user_id"], data.datasource_id,
            data.question, final_state.get("sql"),
            success=final_state.get("success", False),
            row_count=final_state.get("row_count", 0),
            execution_time_ms=elapsed_ms,
            error=final_state.get("error"),
            chart_type=chart_type,
        )

        await db.commit()  # 提交审计日志 + 查询历史

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

        # 成功且有数据时写入缓存
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
        # 管道执行超时，返回友好的错误信息而非 500
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error=f"查询超时（{settings.query_pipeline_timeout}秒限制）",
            execution_time_ms=elapsed_ms,
        )
    except Exception as e:
        # 兜底异常处理：记录完整堆栈，返回通用错误
        # logger.exception() 会自动打印完整的 traceback
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
        raise HTTPException(status_code=400, detail=api_error("EMPTY_SQL", "SQL 不能为空"))

    ds = await _check_datasource(datasource_id, tenant_id, db)

    # Validate: only SELECT allowed
    validation = do_validate(sql)
    if not validation.get("safe", True):
        raise HTTPException(
            status_code=403,
            detail=api_error("UNSAFE_SQL", f"仅允许 SELECT 查询: {validation.get('reason', '')}"),
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
            detail=api_error("TIMEOUT", f"查询超时（{settings.query_pipeline_timeout}秒限制）"),
        )

    columns = final_state.get("columns", [])
    rows = final_state.get("rows", [])

    if not columns or not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("NO_DATA", "无数据可导出"),
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
            detail=api_error("TASK_NOT_FOUND", "异步查询任务不存在"),
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
            detail=api_error("TASK_NOT_FOUND", "异步查询任务不存在"),
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