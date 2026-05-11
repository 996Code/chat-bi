"""
ChatBI 应用入口 — FastAPI 工厂模式

本文件是整个后端的启动核心，职责：
1. 创建 FastAPI 应用实例（工厂函数 create_app）
2. 管理应用生命周期（lifespan）：启动时建表/迁移/清缓存，关闭时释放资源
3. 注册中间件栈：CORS → 安全头 → 限流（注意：中间件执行顺序与注册顺序相反）
4. 挂载所有 API 路由

关键概念：
- 工厂模式：create_app() 返回配置好的 FastAPI 实例，方便测试时创建不同配置的 app
- lifespan：FastAPI 的"上下文管理器"模式，yield 之前是启动逻辑，yield 之后是关闭逻辑
- 中间件栈：后注册的中间件先执行（洋葱模型），所以限流最先拦截请求

关联文件：
- app/core/config.py  — 读取所有配置（settings）
- app/core/redis_client.py — 关闭时释放 Redis 连接
- app/services/connection_pool.py — 关闭时释放数据库连接池
- app/db/base.py — SQLAlchemy 模型基类，用于自动建表
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limiter import rate_limit_middleware

# ---- 路由导入 ----
# 每个路由文件对应一个业务模块，所有路由共享 settings.api_prefix 前缀
from app.api.auth import router as auth_router              # 认证：登录、注册、密码重置
from app.api.datasource import router as datasource_router  # 数据源：CRUD + 连接测试
from app.api.query import router as query_router            # 查询：自然语言转 SQL 的核心接口
from app.api.saved_query import router as saved_query_router # 收藏查询
from app.api.export import router as export_router          # 导出：CSV/Excel
from app.api.audit import router as audit_router            # 审计日志
from app.api.feedback import router as feedback_router      # 用户反馈
from app.api.conversation import router as conversation_router # 对话历史
from app.api.data_model import router as data_model_router  # 数据模型：表/字段元数据管理
from app.api.analytics import router as analytics_router    # 使用统计
from app.api.evaluation import router as eval_router        # AI 评估
from app.api.dashboard import router as dashboard_router    # 看板：可视化仪表盘
from app.api.docs import router as docs_router               # 文档：学习指南

from app.services.connection_pool import pool_manager       # 数据源连接池管理器
from app.core.redis_client import close_redis               # Redis 关闭函数
from app.db.base import Base                                # SQLAlchemy 声明式基类
from app.db.session import engine                           # 异步数据库引擎

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 应用生命周期管理
# ---------------------------------------------------------------------------
# @asynccontextmanager 是 Python 标准库提供的"异步上下文管理器"装饰器。
# FastAPI 用它来管理启动和关闭两个阶段：
#   - yield 之前：应用启动时执行（建表、迁移、清缓存、启动定时任务）
#   - yield 之后：应用关闭时执行（停止定时任务、释放连接池和 Redis）
# 这比旧版 FastAPI 的 on_event("startup") / on_event("shutdown") 更推荐。
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== 启动阶段 =====

    # 1. 自动建表：根据 SQLAlchemy 模型定义创建所有表
    #    run_sync 用于在异步上下文中执行同步的 SQLAlchemy 操作
    #    注意：生产环境应使用 Alembic 做数据库迁移，这里仅用于开发便利
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")

    # 2. 自动迁移：为已有表添加缺失的列
    #    这是一种轻量级的"零停机迁移"策略——只加列不改列，避免破坏性变更
    #    每次迁移先 SHOW COLUMNS 检查列是否已存在，幂等可重复执行
    #    整个迁移包裹在 try/except 中，失败不影响应用启动（非致命）
    try:
        from sqlalchemy import text
        async with engine.begin() as conn:
            # async_queries 表：新增 intent（意图识别结果）和 pipeline_trace（管道追踪）列
            columns_result = await conn.execute(text(
                "SHOW COLUMNS FROM async_queries LIKE 'intent'"
            ))
            if not columns_result.fetchone():
                await conn.execute(text(
                    "ALTER TABLE async_queries ADD COLUMN intent VARCHAR(50) NULL "
                    "AFTER execution_time_ms"
                ))
                logger.info("Auto-migration: added intent column to async_queries")
            columns_result = await conn.execute(text(
                "SHOW COLUMNS FROM async_queries LIKE 'pipeline_trace'"
            ))
            if not columns_result.fetchone():
                await conn.execute(text(
                    "ALTER TABLE async_queries ADD COLUMN pipeline_trace TEXT NULL "
                    "AFTER intent"
                ))
                logger.info("Auto-migration: added pipeline_trace column to async_queries")

            # dashboard_widgets 表：新增 query_sql（看板组件绑定的 SQL）
            columns_result = await conn.execute(text(
                "SHOW COLUMNS FROM dashboard_widgets LIKE 'query_sql'"
            ))
            if not columns_result.fetchone():
                await conn.execute(text(
                    "ALTER TABLE dashboard_widgets ADD COLUMN query_sql TEXT NULL "
                    "AFTER question"
                ))
                logger.info("Auto-migration: added query_sql column to dashboard_widgets")

            # dashboards 表：新增 layout_config（看板布局配置）
            columns_result = await conn.execute(text(
                "SHOW COLUMNS FROM dashboards LIKE 'layout_config'"
            ))
            if not columns_result.fetchone():
                await conn.execute(text(
                    "ALTER TABLE dashboards ADD COLUMN layout_config TEXT NULL "
                    "AFTER name"
                ))
                logger.info("Auto-migration: added layout_config column to dashboards")

            # dashboards 表：新增 datasource_id（看板绑定的数据源）
            columns_result = await conn.execute(text(
                "SHOW COLUMNS FROM dashboards LIKE 'datasource_id'"
            ))
            if not columns_result.fetchone():
                await conn.execute(text(
                    "ALTER TABLE dashboards ADD COLUMN datasource_id CHAR(36) NOT NULL "
                    "AFTER name"
                ))
                logger.info("Auto-migration: added datasource_id column to dashboards")
    except Exception as e:
        logger.warning("Auto-migration failed (non-fatal): %s", e)

    # 3. 清空查询缓存：新版本部署后旧缓存可能过期，启动时统一清除
    from app.services.cache_service import cache_clear_all
    cleared = await cache_clear_all()
    logger.info("Startup: cleared %d cache entries", cleared)

    # 4. 启动元数据自动刷新定时任务
    from app.services.scheduler import start_scheduler
    await start_scheduler()

    # yield 将控制权交还给 FastAPI，应用开始接收请求
    yield

    # ===== 关闭阶段 =====
    # yield 之后的代码在应用关闭时执行，用于释放所有外部资源

    # 停止定时任务
    from app.services.scheduler import stop_scheduler
    await stop_scheduler()
    # 关闭所有数据源的连接池
    await pool_manager.close_all()
    # 关闭 Redis 连接
    await close_redis()
    logger.info("All connection pools and Redis disposed")


# ---------------------------------------------------------------------------
# FastAPI 应用工厂
# ---------------------------------------------------------------------------
# 工厂模式的优势：
# 1. 测试时可以创建不同配置的 app 实例（如关闭中间件、换数据库）
# 2. 延迟初始化——只在调用时才创建，避免模块导入时产生副作用
# 3. 可以创建多个实例（如同时运行 API 版和后台任务版）
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="ChatBI",
        description="Natural language to SQL BI platform",
        version="0.1.0",
        lifespan=lifespan,  # 绑定生命周期管理器
    )

    # ===== 中间件栈 =====
    # 重要：FastAPI/Starlette 的中间件是"洋葱模型"——后注册的中间件先执行
    # 注册顺序：CORS → 安全头 → 限流
    # 执行顺序：限流 → 安全头 → CORS → 路由处理
    # 所以限流最先拦截请求，未通过限流的请求不会到达后续中间件

    # 1. CORS（跨域资源共享）
    #    前端运行在 localhost:5173，后端在 8999，浏览器要求配置 CORS 才能跨域请求
    #    allow_credentials=True：允许携带 Cookie / Authorization 头
    #    allow_methods=["*"]：允许所有 HTTP 方法（GET/POST/PUT/DELETE 等）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. 安全响应头
    #    X-Content-Type-Options: nosniff — 防止浏览器猜测（MIME sniffing）响应类型
    #    X-Frame-Options: DENY — 防止页面被嵌入 iframe（防点击劫持）
    #    Content-Security-Policy: default-src 'self' — 只允许加载同源资源（防 XSS）
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "default-src 'self'"
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # 3. 限流中间件
    #    防止恶意用户高频请求耗尽服务器资源
    #    限流规则在 app/core/rate_limiter.py 中定义，按 IP + 接口类型区分
    from starlette.middleware.base import BaseHTTPMiddleware
    app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_middleware)

    # ===== 路由注册 =====
    # 所有路由共享 settings.api_prefix 前缀（默认 /chat-bi/api/v1）
    # 例如 auth_router 中的 /login 实际路径为 /chat-bi/api/v1/auth/login
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(datasource_router, prefix=settings.api_prefix)
    app.include_router(query_router, prefix=settings.api_prefix)
    app.include_router(saved_query_router, prefix=settings.api_prefix)
    app.include_router(export_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    app.include_router(feedback_router, prefix=settings.api_prefix)
    app.include_router(conversation_router, prefix=settings.api_prefix)
    app.include_router(data_model_router, prefix=settings.api_prefix)
    app.include_router(analytics_router, prefix=settings.api_prefix)
    app.include_router(eval_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router, prefix=settings.api_prefix)
    app.include_router(docs_router, prefix=settings.api_prefix)

    # 健康检查端点：用于 Docker/K8s 探活，不经过 api_prefix
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


# 模块级实例：uvicorn 启动时直接引用 app = create_app()
# 等价于 uvicorn app.main:app
app = create_app()
