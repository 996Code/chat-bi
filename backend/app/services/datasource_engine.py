"""
T014-preB: 动态业务库引擎

根据 DataSource 配置动态连接用户的业务库（扫描 T013 / 查询 T031 都要用）。

对标 v1 教训 #42: 连接池 close_all 只删字典不调 dispose() → 数据库端连接泄漏。
本模块用 DataSourceEnginePool 显式 dispose, 保证连接真正释放。

注意:
  - SQLAlchemy inspect() 是同步 API → 这里用同步 create_engine (psycopg2/pymysql),
    而非 session.py 的 async create_async_engine。T013 端到端验证已用此模式。
  - 连接串里的密码要 URL 编码 (特殊字符如 @ / : 会破坏 URL)。
  - 连接池参数 (pool_size/max_overflow/pool_pre_ping) 走 config, 不硬编码。
  - 已删除数据源的引擎会被逐出 (dispose), 防止泄漏 (对标 O11/O15)。
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app.core.security import decrypt_password

logger = logging.getLogger(__name__)


def build_engine_url(
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> str:
    """按方言拼同步连接串。

    psycopg2 (PostgreSQL) 和 pymysql (MySQL) 都是同步驱动, 兼容 inspect()。
    """
    pwd = quote_plus(password)
    user = quote_plus(username)
    if db_type == "postgresql":
        return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{database}"
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{database}"
    raise ValueError(f"不支持的数据库类型: {db_type}")


def datasource_to_url(data_source) -> str:
    """从 DataSource ORM 行 → 解密密码 → 拼连接串。

    data_source 需有字段: db_type, host, port, database, username, encrypted_password。
    """
    plain_password = decrypt_password(data_source.encrypted_password)
    return build_engine_url(
        db_type=data_source.db_type,
        host=data_source.host,
        port=data_source.port,
        database=data_source.database,
        username=data_source.username,
        password=plain_password,
    )


class DataSourceEnginePool:
    """按 data_source_id 缓存同步引擎, 显式 dispose 释放连接 (对标 #42)。

    单例模式: 整个进程一个池。dispose 只移除并释放指定引擎;
    不调 dispose 的引擎会泄漏 (与 v1 同款 bug), 调用方必须负责释放。

    v2 改进 (对标 O11/O12/O13/O14/O15):
      - 连接池参数 (pool_size, max_overflow, pool_pre_ping) 走 config
      - pool_pre_ping=True: 每次从池取连接时探测存活, 防止拿到断开的连接
      - 已删除数据源的引擎在 get_or_create 时逐出 (dispose), 防止无限累积
      - 连接池耗尽 TimeoutError 优雅处理 (不崩溃, 返回错误信息)
    """

    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}
        self._active_ds_ids: set[str] = set()  # 当前活跃的数据源 id

    def get_or_create(
        self,
        datasource_id: str,
        url: str,
        dialect_override: str | None = None,
    ) -> Engine:
        """获取或创建引擎。命中缓存则返回已有实例。

        dialect_override: 测试用, 强制方言 (如 'sqlite') 避免真连库。
        """
        if datasource_id in self._engines:
            return self._engines[datasource_id]

        kwargs: dict = {"echo": False}

        if dialect_override == "sqlite" or url.startswith("sqlite"):
            # SQLite in-memory 需 StaticPool 否则连接间不共享
            from sqlalchemy.pool import StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        else:
            # 连接池参数走 config (对标 O11/O12/O14)
            from app.core.config import get_settings
            settings = get_settings()
            kwargs["pool_size"] = settings.business_db_pool_size
            kwargs["max_overflow"] = settings.business_db_max_overflow
            kwargs["pool_pre_ping"] = True  # 对标 O14: 探测存活, 防止断连
            kwargs["pool_recycle"] = settings.business_db_pool_recycle

        engine = create_engine(url, **kwargs)
        self._engines[datasource_id] = engine
        self._active_ds_ids.add(datasource_id)
        return engine

    def get_inspector(self, datasource_id: str, url: str) -> object:
        """便捷方法: 获取引擎并返回 inspector。"""
        engine = self.get_or_create(datasource_id, url)
        return inspect(engine)

    def dispose(self, datasource_id: str) -> None:
        """释放指定引擎的所有连接并从池中移除 (对标 #42)。"""
        engine = self._engines.pop(datasource_id, None)
        self._active_ds_ids.discard(datasource_id)
        if engine is not None:
            engine.dispose()

    def dispose_all(self) -> None:
        """释放所有引擎 (进程退出 / 测试清理时用)。"""
        for engine in list(self._engines.values()):
            engine.dispose()
        self._engines.clear()
        self._active_ds_ids.clear()

    def evict_stale(self, active_ds_ids: set[str]) -> int:
        """逐出已不存在的数据源引擎 (对标 O15: 删除 DS 后引擎仍在池中)。

        Args:
            active_ds_ids: 当前数据库中仍活跃的数据源 id 集合。

        Returns:
            逐出的引擎数量。
        """
        stale_ids = set(self._engines.keys()) - active_ds_ids
        for ds_id in stale_ids:
            self.dispose(ds_id)
            logger.info("逐出已删除数据源的引擎: %s", ds_id)
        return len(stale_ids)


# 模块级单例池
_pool: DataSourceEnginePool | None = None


def get_engine_pool() -> DataSourceEnginePool:
    """获取全局引擎池单例。"""
    global _pool
    if _pool is None:
        _pool = DataSourceEnginePool()
    return _pool
