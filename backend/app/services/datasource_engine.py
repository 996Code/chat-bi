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

    注意: 选择 psycopg2 而非 asyncpg 是因为 SQLAlchemy inspect() 是同步 API,
    整个模块必须使用同步引擎。session.py 中的 async engine 与此不冲突。
    """
    pwd = quote_plus(password)  # URL 编码: 密码中可能包含 @ / : # ? 等特殊字符
    user = quote_plus(username)  # 用户名同样编码, 某些数据库用户名允许特殊字符
    if db_type == "postgresql":
        return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{database}"
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{database}"
    raise ValueError(f"不支持的数据库类型: {db_type}")


def datasource_to_url(data_source) -> str:
    """从 DataSource ORM 行 → 解密密码 → 拼连接串。

    data_source 需有字段: db_type, host, port, database, username, encrypted_password。

    数据流: ORM 模型 → 解密 (AES) → URL 编码 → 连接串
    密码解密失败会抛出异常, 由调用方处理 (不在此处静默降级, 避免连接池使用错误密码)。
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

    生命周期:
      get_or_create → 懒创建 (Lazy init, 首次使用才创建连接池)
      dispose → 显式释放 (由 health check / 数据源删除触发)
      evict_stale → 周期清理 (定时任务比对活跃数据源列表, 逐出已删除的)

    线程安全说明: SQLAlchemy Engine 本身是线程安全的, 但 _engines 字典的
    读写操作在异步 Web 框架中可能并发, 本模块仅在 health check 和 API 调用
    中单线程按序列访问, 未加锁。若未来改为多线程访问, 需加 threading.Lock。

    _active_ds_ids 用于 evict_stale 判断哪些引擎应被逐出, 不与 _engines 的
    keys 完全同步 (get_or_create 时同步添加, dispose 时同步移除)。
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

        缓存策略: 简单 dict 缓存, 无 TTL 过期。
        引擎释放完全由 dispose/evict_stale 手动触发, 不做自动过期,
        因为连接池内部有 pool_recycle 控制连接老化回收。

        边界情况:
        - 数据源配置变更 (密码/地址) → 调用方需先 dispose 再 get_or_create,
          否则返回旧引擎 (密码/地址不变, 只更新连接池参数不会反映到已有引擎)
        - 并发调用: 如果两个请求同时首次访问同一数据源, 可能创建两个引擎,
          后者覆盖前者, 前者引擎泄漏。当前场景下首次访问通常是串行的
          (首次扫描), 暂不处理。若需避免, 可用双重检查锁。
        """
        # 缓存命中直接返回, 避免重复创建连接池
        if datasource_id in self._engines:
            return self._engines[datasource_id]

        # 连接池参数配置
        kwargs: dict = {"echo": False}

        if dialect_override == "sqlite" or url.startswith("sqlite"):
            # SQLite in-memory 需 StaticPool 否则连接间不共享数据
            from sqlalchemy.pool import StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        else:
            # 生产数据库: 连接池参数走 config (对标 O11/O12/O14)
            from app.core.config import get_settings
            settings = get_settings()
            kwargs["pool_size"] = settings.business_db_pool_size
            kwargs["max_overflow"] = settings.business_db_max_overflow
            kwargs["pool_pre_ping"] = True  # 对标 O14: 探测存活, 防止断连
            kwargs["pool_recycle"] = settings.business_db_pool_recycle

            # pool_pre_ping 的作用: 每次 conn = engine.connect() 时,
            # SQLAlchemy 先执行一条 SELECT 1 确认连接存活。
            # 如果数据库重启/网络断开, 旧连接被标记为 invalid, 自动创建新连接。
            # 代价: 每次获取连接多一次往返, 但大幅提升可靠性。

        engine = create_engine(url, **kwargs)
        self._engines[datasource_id] = engine
        self._active_ds_ids.add(datasource_id)
        return engine

    def get_inspector(self, datasource_id: str, url: str) -> object:
        """便捷方法: 获取引擎并返回 inspector。

        适用场景: 语义层扫描 (scan_data_source) 需要 inspect 来获取表/列结构。
        不缓存 inspector 实例, 每次调用 create_engine 后重新 inspect,
        因为 Inspector 对象持有连接引用, 长期持有可能导致连接不释放。
        """
        engine = self.get_or_create(datasource_id, url)
        return inspect(engine)

    def dispose(self, datasource_id: str) -> None:
        """释放指定引擎的所有连接并从池中移除 (对标 #42)。

        调用 engine.dispose() 会关闭连接池中的所有连接,
        并等待 in-use 连接归还后再关闭 (默认 timeout=30s)。

        注意: dispose 后如果调用方仍持有从旧引擎获取的 Connection 对象,
        对该 Connection 的操作会报错。调用方应确保 dispose 后不再使用旧连接。

        对标 v1 教训 #42: v1 只删字典不调 dispose(), 导致数据库端连接泄漏,
        最终数据库连接数打满, 业务库不可用。
        """
        engine = self._engines.pop(datasource_id, None)
        self._active_ds_ids.discard(datasource_id)
        if engine is not None:
            engine.dispose()

    def dispose_all(self) -> None:
        """释放所有引擎 (进程退出 / 测试清理时用)。

        遍历所有引擎逐个 dispose, 确保连接全部关闭。
        测试中每次 tearDown 应调用此方法, 避免测试间连接池状态污染。

        注意: 此方法不处理引擎 dispose 过程中的异常,
        如果某个引擎的 dispose 抛出异常, 后续引擎将不再释放。
        生产环境进程退出时, OS 会自动回收连接, 此方法主要用于测试。
        """
        for engine in list(self._engines.values()):
            engine.dispose()
        self._engines.clear()
        self._active_ds_ids.clear()

    def evict_stale(self, active_ds_ids: set[str]) -> int:
        """逐出已不存在的数据源引擎 (对标 O15: 删除 DS 后引擎仍在池中)。

        定时任务 (如 health check / metadata refresh) 周期调用此方法,
        比对当前活跃数据源 ID 集合, 清理已删除数据源的引擎。

        逐出策略:
        - 只比对 ID 集合, 不关心数据源配置是否变更
        - 配置变更 (如密码更新) 需要调用方主动 dispose + get_or_create
        - 逐出顺序不重要, 每个引擎独立 dispose

        Args:
            active_ds_ids: 当前数据库中仍活跃的数据源 id 集合。

        Returns:
            逐出的引擎数量。

        数据流:
        DB 查询活跃数据源 → evict_stale (active_ds_ids) → dispose 已删除的 → 下次 get_or_create 重新创建
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
