"""
ChatBI v2 — Database Session Management

Engine creation is lazy to support SQLite in tests (no pool_size for SQLite).

对标: v1 db/session.py — SQLAlchemy async session factory

自动建表: get_engine() 后调 auto_create_tables(), 根据模型定义自动创建缺失表。
  - PostgreSQL: 用 create_all() 建表 + 增量 ALTER ADD COLUMN 补新列
  - SQLite: 仅 create_all() (测试用, 不做 ALTER)
  - init-chatbi.sql 退化为种子数据脚本, 表结构以 models.py 为单一真相源
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import UniqueConstraint

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""
    pass


_engine = None
_async_session_factory = None
_engine_lock: asyncio.Lock | None = None


def _get_engine_lock() -> asyncio.Lock:
    """Lazy-create asyncio.Lock (must be created inside a running event loop)."""
    global _engine_lock
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()
    return _engine_lock


def _get_engine_kwargs(database_url: str, debug: bool) -> dict:
    """Build engine kwargs based on database type.

    SQLite (used in tests) doesn't support pool_size/max_overflow.
    PostgreSQL/MySQL: pool_size/max_overflow from config (对标 O12: 不硬编码)。
    """
    kwargs = {"echo": debug}

    is_sqlite = "sqlite" in database_url
    is_postgresql = "postgresql" in database_url or "asyncpg" in database_url

    if is_sqlite:
        # SQLite: use StaticPool, no pool_size kwargs
        kwargs["connect_args"] = {"check_same_thread": False}
    elif is_postgresql or "mysql" in database_url or "aiomysql" in database_url:
        from app.core.config import get_settings
        settings = get_settings()
        kwargs.update({
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_pre_ping": True,
            "pool_recycle": settings.business_db_pool_recycle,  # 对标审计: 元数据 DB 也需连接回收
        })

    return kwargs


async def get_engine():
    """Get or create the async SQLAlchemy engine (lazy init, thread-safe)."""
    global _engine
    if _engine is not None:
        return _engine

    async with _get_engine_lock():
        if _engine is not None:  # Double-check after acquiring lock
            return _engine
        from app.core.config import get_settings
        settings = get_settings()
        kwargs = _get_engine_kwargs(settings.database_url, settings.debug)
        _engine = create_async_engine(settings.database_url, **kwargs)
        return _engine


def get_async_session_factory():
    """Get or create the async session factory (lazy init)."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_engine_sync(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


def get_engine_sync():
    """Synchronous engine access for cases where async is not available."""
    global _engine
    if _engine is not None:
        return _engine
    from app.core.config import get_settings
    settings = get_settings()
    kwargs = _get_engine_kwargs(settings.database_url, settings.debug)
    _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


async def get_db() -> AsyncSession:
    """FastAPI dependency: yield an async database session."""
    await get_engine()  # Ensure engine is initialized (async-safe)
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def auto_create_tables():
    """根据模型定义自动创建缺失表 + 增量补列.

    - create_all() 建缺失的表 (已存在的表不受影响)
    - PostgreSQL: 逐表检查已有列, ALTER ADD COLUMN 补模型中新加的列
    - SQLite: 仅 create_all() (测试用, 不做 ALTER — SQLite ALTER 支持有限)
    - 枚举类型: PostgreSQL 自动补缺 (CREATE TYPE ... IF NOT EXISTS)

    设计原则: 模型(models.py)是表结构的单一真相源, init-chatbi.sql 仅负责种子数据。
    """
    engine = await get_engine()
    is_sqlite = "sqlite" in str(engine.url)

    # 1. create_all() — 建缺失的表 (已存在的表不会重建/不会丢数据)
    async with engine.begin() as conn:
        # PostgreSQL: 先确保枚举类型存在
        if not is_sqlite:
            await _ensure_enum_types(conn)
        await conn.run_sync(Base.metadata.create_all)

    # 2. PostgreSQL 增量补列 — 模型新加的列自动 ALTER ADD
    if not is_sqlite:
        async with engine.begin() as conn:
            await _add_missing_columns(conn)
            await _add_missing_constraints(conn)

    logger.info("auto_create_tables: 表结构同步完成")


async def _ensure_enum_types(conn):
    """确保 PostgreSQL 枚举类型存在 (CREATE TYPE IF NOT EXISTS 等效)."""
    # 从所有模型收集枚举类型定义
    enum_types: dict[str, list[str]] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            type_name = getattr(col.type, "name", None)
            if type_name and hasattr(col.type, "enums"):
                enum_types[type_name] = list(col.type.enums)

    for type_name, values in enum_types.items():
        # PostgreSQL: DO block 实现 IF NOT EXISTS
        values_sql = ", ".join(f"'{v}'" for v in values)
        await conn.execute(text(
            f"DO $$ BEGIN "
            f"CREATE TYPE {type_name} AS ENUM ({values_sql}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; "
            f"END $$;"
        ))


async def _add_missing_columns(conn):
    """检查每个模型表, 补上模型中有但表中没有的列 (ALTER ADD COLUMN)."""
    for table in Base.metadata.tables.values():
        # 获取表中已有的列名
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table_name AND table_schema = current_schema()"
        ), {"table_name": table.name})
        existing_cols = {row[0] for row in result}

        for col in table.columns:
            if col.name not in existing_cols:
                col_type = col.type.compile(dialect=conn.dialect)
                nullable = "" if col.nullable else " NOT NULL"
                default = ""
                if col.server_default is not None:
                    default = f" DEFAULT {col.server_default.arg}"
                elif col.default is not None:
                    # Python-side default: 用 SQL 友好的值
                    val = col.default.arg
                    if callable(val):
                        continue  # 跳过 callable default (如 new_uuid), 启动后 ORM 层处理
                    if isinstance(val, bool):
                        default = f" DEFAULT {'TRUE' if val else 'FALSE'}"
                    elif isinstance(val, int):
                        default = f" DEFAULT {val}"
                    elif isinstance(val, str):
                        default = f" DEFAULT '{val}'"

                await conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}{nullable}{default}'
                ))
                logger.info("auto_create_tables: 补列 %s.%s", table.name, col.name)


async def _add_missing_constraints(conn):
    """检查模型定义的 UniqueConstraint, 补上表中没有的约束 (ALTER ADD CONSTRAINT).

    先去重已有数据 (保留 id 最大的行, 即最新记录更完整), 再加约束,
    避免重复数据导致 ADD CONSTRAINT 失败。
    """
    for table in Base.metadata.tables.values():
        # 检查表是否有 id 列 (去重 SQL 依赖它)
        has_id = any(col.name == "id" for col in table.columns)
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            constraint_name = constraint.name
            if not constraint_name:
                continue
            # 检查约束是否已存在
            result = await conn.execute(text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = :table_name AND constraint_type = 'UNIQUE' "
                "AND table_schema = current_schema() AND constraint_name = :constraint_name"
            ), {"table_name": table.name, "constraint_name": constraint_name})
            if result.fetchone():
                continue  # 约束已存在
            # 构建列列表
            cols = ", ".join(f'"{col.name}"' for col in constraint.columns)
            # 先去重: 删除重复行 (保留 id 最大的, 即最新记录更完整)
            if has_id:
                col_names = [col.name for col in constraint.columns]
                dedup_condition = " AND ".join(
                    f'a."{c}" IS NOT DISTINCT FROM b."{c}"' for c in col_names
                )
                try:
                    await conn.execute(text(
                        f'DELETE FROM "{table.name}" a USING "{table.name}" b '
                        f'WHERE a.id < b.id AND {dedup_condition}'
                    ))
                except Exception as e:
                    logger.warning("auto_create_tables: 去重失败 %s: %s", table.name, e)
            # 添加约束
            try:
                await conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD CONSTRAINT "{constraint_name}" UNIQUE ({cols})'
                ))
                logger.info("auto_create_tables: 补约束 %s.%s", table.name, constraint_name)
            except Exception as e:
                logger.warning("auto_create_tables: 补约束失败 %s.%s: %s", table.name, constraint_name, e)
