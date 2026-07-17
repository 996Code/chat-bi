"""
ChatBI v2 — Test Configuration & Fixtures

连接真实 PostgreSQL (chatbi_test 库), 而非 SQLite。
隔离: 每个测试在独立事务中执行, 结束回滚 (不污染数据, 不依赖 DELETE 清理)。

前置: 本地 PG 已起, chatbi_test 库已建表 (见 backend/tests/README 或 conftest 注释)。
对标 AGENTS.md: "测试用 SQLite in-memory" → 改为真实 PG (用户要求全切真实中间件)。

关键设计: 用同步 psycopg2 做 TRUNCATE + seed, 避免 asyncpg 和 pytest-asyncio
event loop 的死锁问题。asyncpg 只用于测试内的 async db_session。
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

# ── 测试环境标记 (必须在任何 app import 之前) ───────────────────
# main.py 模块级 app = create_app() 会触发 lifespan (启动探测/embedder),
# 测试时需跳过。此标记让 main.py 检测到测试环境, 使用 noop lifespan。
os.environ["CHATBI_TESTING"] = "1"

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── 测试环境配置 (连真实 PG chatbi_test 库) ───────────────────
_TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://root:root@localhost:5432/chatbi_test",
)
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["SECRET_KEY"] = "test-secret-key-change-in-production-abcdef123456"
os.environ["FERNET_KEY"] = "3OO-go6es96rvMajcdliCWYpXiwvZ_Sckkpe0pQKF40="
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://:redis_pass@localhost:6379/0")
os.environ["MILVUS_URL"] = os.getenv("TEST_MILVUS_URL", "http://localhost:19530")
os.environ["MILVUS_TOKEN"] = os.getenv("TEST_MILVUS_TOKEN", "root:Milvus")
os.environ["VECTOR_STORE_COLLECTION_PREFIX"] = "test_"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DEBUG"] = "true"
os.environ["ENV_FILE"] = ""
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["LLM_URL"] = os.getenv("TEST_LLM_URL", "http://localhost:11434/v1")
os.environ["LLM_MODEL"] = os.getenv("TEST_LLM_MODEL", "test-model")
os.environ["LLM_API_KEY"] = os.getenv("TEST_LLM_API_KEY", "test-llm-key")

from app.core.config import get_settings
get_settings.cache_clear()

# 同步 PG URL (psycopg2 用, TRUNCATE + seed)
_SYNC_DB_URL = _TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")

from sqlalchemy.pool import NullPool


# ── 建表 (session scope, 同步) ──────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    """session 开始用同步 psycopg2 建表 (幂等)。

    用 psycopg2 而非 asyncpg, 避免 asyncio.run() 和 pytest-asyncio event loop 冲突。
    """
    import app.db.models  # noqa: F401 — 注册所有 model 到 metadata
    from app.db.session import Base
    import psycopg2
    from sqlalchemy import create_engine as sync_create_engine

    # 用同步引擎建表 (SQLAlchemy + psycopg2)
    sync_engine = sync_create_engine(_SYNC_DB_URL, echo=False)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    yield


# ── async engine (供 db_session / http_client 用) ──────────────

@pytest.fixture(scope="session")
def test_engine():
    """共享的测试 PG 引擎 (连 chatbi_test 库, NullPool 避免 event loop 跨域)。"""
    engine = create_async_engine(_TEST_DB_URL, echo=False, poolclass=NullPool)
    return engine


# ── 事务隔离的 db_session ─────────────────────────────────────

@pytest.fixture
async def db_session(test_engine, _create_tables) -> AsyncGenerator[AsyncSession, None]:
    """每个测试用独立 async session。

    隔离靠 _seed_base_tenants 的 TRUNCATE (每个测试开始前清空, 保证幂等)。
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
        try:
            await session.rollback()
        except Exception:
            pass
        await session.close()


# ── TRUNCATE + seed (同步, autouse) ───────────────────────────

@pytest.fixture(autouse=True)
async def _seed_base_tenants(db_session, _create_tables):
    """每个 test 前自动清空 + 重建, 保证幂等。

    用同步 psycopg2 做 (via asyncio.to_thread)。
    用 DELETE 代替 TRUNCATE: DELETE 只需 RowExclusiveLock, 不会和 asyncpg 的
    行锁产生 AccessExclusiveLock 死锁。TRUNCATE 需要 AccessExclusiveLock,
    和 asyncpg 的 RowExclusiveLock 互相等待 → 死锁。
    """
    import psycopg2
    from app.core.security import hash_password

    # 先释放 db_session 可能持有的行锁
    try:
        await db_session.rollback()
    except Exception:
        pass
    try:
        await db_session.close()
    except Exception:
        pass

    def _do_seed():
        conn = psycopg2.connect(_SYNC_DB_URL)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            # DELETE 代替 TRUNCATE: 不需要 AccessExclusiveLock, 避免死锁
            # 按外键依赖顺序删除 (子表先删)
            for table in [
                "audit_logs", "saved_queries", "dashboard_widgets", "dashboards",
                "conversations", "semantic_models", "data_sources", "users", "tenants",
            ]:
                cur.execute(f"DELETE FROM {table}")
            # 重置序列 (TRUNCATE RESTART IDENTITY 的等价操作)
            for table in [
                "audit_logs", "saved_queries", "dashboard_widgets", "dashboards",
                "conversations", "semantic_models", "data_sources", "users", "tenants",
            ]:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 1, false) "
                    f"WHERE EXISTS (SELECT 1 FROM pg_get_serial_sequence('{table}', 'id'))"
                )
            tenants = ["tenant_A", "tenant_B", "default_tenant", "t1", "t2", "t3"]
            for tid in tenants:
                cur.execute(
                    "INSERT INTO tenants (id, name, is_active, created_at, updated_at) VALUES (%s, %s, true, now(), now()) "
                    "ON CONFLICT (id) DO NOTHING",
                    (tid, f"测试租户 {tid}"),
                )
            pw = hash_password("test")
            seed_users = [
                ("admin_1", "tenant_A", "admin"), ("user_1", "tenant_A", "user"),
                ("ro_1", "tenant_A", "read_only"), ("admin_2", "tenant_B", "admin"),
            ]
            for uid, tid, role in seed_users:
                cur.execute(
                    "INSERT INTO users (id, tenant_id, email, username, hashed_password, role, is_active, email_verified, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, true, true, now(), now()) "
                    "ON CONFLICT (id) DO NOTHING",
                    (uid, tid, f"{uid}@test.com", uid, pw, role),
                )
            cur.close()
        finally:
            conn.close()

    import asyncio
    await asyncio.to_thread(_do_seed)


@pytest.fixture
def settings():
    """Get test settings (被 test_integration 等引用)。"""
    from app.core.config import get_settings
    return get_settings()


# ── App fixture ────────────────────────────────────────────────

@pytest.fixture
async def http_client(app):
    """HTTP 测试客户端 (走真实 PG)。"""
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def app(test_engine, _create_tables):
    """Test app, mock lifespan 跳过启动探测/embedder/调度器。"""
    from app.db import session as session_module
    from app.main import create_app
    from contextlib import asynccontextmanager

    session_module._engine = test_engine
    session_module._async_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    app = create_app(lifespan_override=_test_lifespan)
    yield app

    session_module._engine = None
    session_module._async_session_factory = None
