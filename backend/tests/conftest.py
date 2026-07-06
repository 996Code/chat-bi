"""
ChatBI v2 — Test Configuration & Fixtures

连接真实 PostgreSQL (chatbi_test 库), 而非 SQLite。
隔离: 每个测试在独立事务中执行, 结束回滚 (不污染数据, 不依赖 DELETE 清理)。

前置: 本地 PG 已起, chatbi_test 库已建表 (见 backend/tests/README 或 conftest 注释)。
对标 AGENTS.md: "测试用 SQLite in-memory" → 改为真实 PG (用户要求全切真实中间件)。
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── 测试环境配置 (连真实 PG chatbi_test 库) ───────────────────
# 覆盖 DATABASE_URL 指向测试库 (不污染生产 chatbi 库)
# 通过环境变量可覆盖 (CI 用不同连接串)
_TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://root:root@localhost:5432/chatbi_test",
)
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["SECRET_KEY"] = "test-secret-key-change-in-production-abcdef123456"
os.environ["FERNET_KEY"] = "3OO-go6es96rvMajcdliCWYpXiwvZ_Sckkpe0pQKF40="
# 真实中间件 (docker infra): Redis/Milvus 连本地容器, 测试走真实中间件 (非 mock)
# 对标"全切真实中间件"目标; probe/milvus 索引测试需要
# Redis 密码默认 redis_pass (docker-compose.infra.yml REDIS_PASSWORD), 可被 TEST_REDIS_URL 覆盖
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://:redis_pass@localhost:6379/0")
os.environ["MILVUS_URL"] = os.getenv("TEST_MILVUS_URL", "http://localhost:19530")
os.environ["MILVUS_TOKEN"] = os.getenv("TEST_MILVUS_TOKEN", "root:Milvus")
# 向量数据隔离 (对标 PG 库隔离): 测试 collection 加 test_ 前缀,
# 和开发/生产数据物理隔离 (Milvus 里 test_semantic_models ≠ semantic_models)。
# 一次配置, get_vector_store 全局生效, 不依赖调用方记得带前缀。
os.environ["VECTOR_STORE_COLLECTION_PREFIX"] = "test_"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DEBUG"] = "true"  # 测试环境等同开发模式 (跳过 email_verified 强制校验)
os.environ["ENV_FILE"] = ""  # 不加载 .env (用上面的测试配置)
# LLM 占位符 (测试不调真实 LLM, 但 validate_settings_on_startup 检查占位符 → 填测试值)
os.environ["LLM_URL"] = os.getenv("TEST_LLM_URL", "http://localhost:11434/v1")
os.environ["LLM_MODEL"] = os.getenv("TEST_LLM_MODEL", "test-model")
os.environ["LLM_API_KEY"] = os.getenv("TEST_LLM_API_KEY", "test-llm-key")

# Clear the lru_cache to ensure environment variables take effect
from app.core.config import get_settings
get_settings.cache_clear()


# ── Engine & 建表 (session scope) ──────────────────────────────

# session 级引擎: PG 连接池复用, 但 asyncpg 连接绑定 event loop。
# pytest-asyncio 默认每测试一个新 loop → session engine 的连接跨 loop 失效。
# 解决: 用 NullPool (每次从 engine 取连接时新建, 不缓存绑定旧 loop 的连接)。
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="session")
def test_engine():
    """共享的测试 PG 引擎 (连 chatbi_test 库, NullPool 避免 event loop 跨域)。"""
    engine = create_async_engine(_TEST_DB_URL, echo=False, poolclass=NullPool)
    return engine


@pytest.fixture(scope="session", autouse=True)
def _create_tables(test_engine):
    """session 开始建表 (幂等, 已存在跳过) + 补缺失列。

    不在 session 结束 drop (避免 asyncpg event loop 关闭后清理报错)。
    表留着下次 create_all 幂等跳过; 如需重建跑前手动 drop。
    隔离靠每测试事务回滚 (db_session fixture), 不靠 drop/recreate。

    补缺失列: create_all 不 ALTER 已有表 (新列不会被自动加),
    用 _add_missing_columns 补丁 (对标 T065 auto_create_tables 同理)。
    """
    import app.db.models  # noqa: F401 — 注册所有 model 到 metadata
    from app.db.session import Base
    from app.db.session import _add_missing_columns
    import asyncio

    async def _setup():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # 补缺失列 (PG only, SQLite 不需要)
            await _add_missing_columns(conn)

    asyncio.run(_setup())
    yield


# ── 事务隔离的 db_session (每个测试独立事务, 结束回滚) ─────────

@pytest.fixture
async def db_session(test_engine, _create_tables) -> AsyncGenerator[AsyncSession, None]:
    """每个测试用独立 session, 测试后 TRUNCATE 清空所有表 (PG 隔离)。

    PG READ COMMITTED 下, HTTP 请求和 db_session 是不同事务, 互不可见未提交数据。
    → 测试内预置数据需 commit 才能让 HTTP 看到。
    → 隔离靠 TRUNCATE (每个测试结束清空, 下个测试从空表开始)。

    比 SQLite 的 StaticPool 共享连接更真实, 也暴露了 PG 特有约束 (外键/枚举/大小写)。
    """
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    session = async_session()
    try:
        yield session
    finally:
        await session.close()
        # TRUNCATE 清空所有业务表 (CASCADE 处理外键), 重启序列保证 id 一致
        async with test_engine.begin() as conn:
            await conn.execute(text(
                "TRUNCATE TABLE audit_logs, saved_queries, dashboard_widgets, dashboards, "
                "conversations, semantic_models, data_sources, users, tenants "
                "RESTART IDENTITY CASCADE"
            ))


@pytest.fixture(autouse=True)
async def _seed_base_tenants(db_session):
    """PG 强制外键约束 (SQLite 默认不强制), 测试引用的 tenant/user 必须先存在。

    预置测试套件中"被引用但不在测试内自建"的 tenant + user, 避免 FK 违反。
    每个 test 前自动建 (TRUNCATE 后重建, 保证幂等)。

    原则: 这里只 seed"全局测试约定"的固定 ID (token 里的 user_id、audit 的 tenant_id)。
    测试内自建的租户 (如 test_slow_query 的 tenant_audit/bwd) 用独特 ID 自行 add,
    不进 seed — 避免和 seed 撞 pkey。
    """
    from app.db.models import Tenant, User
    from app.core.security import hash_password

    # 测试套件引用的全部租户: tenant_A/B (主), default_tenant, t1/t2/t3 (多租户隔离测试)
    for tid in ["tenant_A", "tenant_B", "default_tenant", "t1", "t2", "t3"]:
        db_session.add(Tenant(id=tid, name=f"测试租户 {tid}"))
    await db_session.flush()
    # 测试套件引用的全部用户 (token/audit 直接引用的固定 ID), 密码随便 (测试用 token 不走登录)
    # admin_1/user_1/ro_1 → tenant_A; admin_2 → tenant_B (跨租户隔离测试)
    seed_users = [
        ("admin_1", "tenant_A", "admin"), ("user_1", "tenant_A", "user"),
        ("ro_1", "tenant_A", "read_only"), ("admin_2", "tenant_B", "admin"),
    ]
    for uid, tid, role in seed_users:
        db_session.add(User(
            id=uid, tenant_id=tid, email=f"{uid}@test.com", username=uid,
            hashed_password=hash_password("test"), role=role, is_active=True, email_verified=True,
        ))
    await db_session.commit()


@pytest.fixture
def settings():
    """Get test settings (被 test_integration 等引用)。"""
    from app.core.config import get_settings
    return get_settings()


# ── App fixture ────────────────────────────────────────────────

@pytest.fixture
async def http_client(app):
    """HTTP 测试客户端 (连同一 test_engine, 走真实 PG)。

    关键: app 的 get_db() 走全局 session_factory, 我们指向 test_engine。
    HTTP 请求和 db_session 各开独立事务 (PG READ COMMITTED 互不可见未提交),
    所以"预置数据"需在 fixture 里 commit (而非依赖 db_session 的回滚隔离)。

    为简化: 多数测试用 HTTP 直接造数据 + HTTP 验证 (自闭环, 不需 db_session)。
    """
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def app(test_engine, _create_tables):
    """Create FastAPI test app, 全局 session_factory 指向 test_engine (连真实 PG)。"""
    from app.db import session as session_module
    from app.main import create_app

    session_module._engine = test_engine
    session_module._async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )

    app = create_app()
    yield app

    # 清理全局状态
    session_module._engine = None
    session_module._async_session_factory = None
