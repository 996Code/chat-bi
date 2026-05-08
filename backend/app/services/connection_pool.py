"""
多数据库连接池管理器 — 为每个用户接入的外部数据源动态创建和管理 SQLAlchemy 异步引擎。

核心概念：
  - ConnectionPoolManager：管理多个数据源的连接池，每个数据源一个独立的 AsyncEngine。
    与 app/db/session.py 的 engine 不同：
      - session.py 的 engine 连接的是 ChatBI 自身的数据库（存用户、查询等业务数据）
      - 本文件的 engine 连接的是用户接入的外部数据库（存业务报表数据）

  - 动态引擎创建：用户每接入一个数据源，就创建一个新的 AsyncEngine。
    引擎按数据源 ID 缓存在 _pools 字典中，避免重复创建。

  - 安全的 URL 构建：使用 SQLAlchemy 的 URL.create() 方法构建连接 URL，
    而不是字符串拼接。好处：
      1. 自动处理特殊字符（密码中的 @、# 等不会破坏 URL 格式）
      2. quote_plus 对用户名和密码做 URL 编码
      3. 避免 SQL 注入式的连接字符串攻击

  - 凭据解密：数据源的用户名和密码在数据库中是加密存储的，
    创建连接时需要先解密（decrypt_value）。

本文件与其它文件的关系：
  - app/db/models.py → DataSource 模型（提供数据源连接信息）
  - app/core/config.py → 连接池参数配置
  - app/core/encryption.py → decrypt_value（解密数据库凭据）
  - app/services/query_service.py → 执行查询时通过 pool_manager 获取引擎
"""
import urllib.parse
import uuid
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import URL, text

from app.core.logging import get_logger
from app.core.config import settings
from app.core.encryption import decrypt_value
from app.db.models import DataSource

logger = get_logger(__name__)


def _build_url(ds: DataSource) -> URL:
    """根据数据源配置构建 SQLAlchemy 连接 URL。

    为什么用 URL.create() 而不是字符串拼接？
      字符串拼接容易出错：如果密码包含特殊字符（如 @、:、/），
      会导致 URL 解析错误。URL.create() 会自动处理这些情况。

    支持的数据库及对应的异步驱动：
      - MySQL → mysql+aiomysql（aiomysql 是 MySQL 的异步驱动）
      - PostgreSQL → postgresql+asyncpg（asyncpg 是 PostgreSQL 的异步驱动）
      - SQLite → sqlite+aiosqlite（aiosqlite 是 SQLite 的异步驱动）

    参数：
        ds: DataSource 模型实例，包含数据库类型、主机、端口、凭据等信息

    返回值：
        URL — SQLAlchemy 的 URL 对象，可以传给 create_async_engine()
    """
    # 解密存储的凭据，然后做 URL 编码
    # quote_plus 将特殊字符转为 %XX 格式，例如 @ → %40，防止破坏 URL 结构
    username = quote_plus(decrypt_value(ds.username_encrypted))
    password = quote_plus(decrypt_value(ds.password_encrypted))

    if ds.db_type == "postgresql":
        return URL.create(
            "postgresql+asyncpg",
            username=username,
            password=password,
            host=ds.host,
            port=ds.port,
            database=ds.database_name,
        )
    elif ds.db_type == "sqlite":
        # SQLite 只需要数据库文件路径，不需要主机/端口/用户名
        return URL.create("sqlite+aiosqlite", database=ds.database_name)
    else:
        # 默认 MySQL，额外指定 charset=utf8mb4 以支持中文和 emoji
        return URL.create(
            "mysql+aiomysql",
            username=username,
            password=password,
            host=ds.host,
            port=ds.port,
            database=ds.database_name,
            query={"charset": "utf8mb4"},
        )


class ConnectionPoolManager:
    """多数据库连接池管理器 — 为每个数据源维护一个独立的连接池。

    设计思路：
      - 每个数据源（DataSource）对应一个 AsyncEngine（连接池）
      - 引擎按数据源 ID 缓存在 _pools 字典中
      - 首次访问时创建引擎，后续访问直接复用
      - 数据源配置变更时，通过 refresh_pool() 销毁旧引擎、创建新引擎

    为什么每个数据源一个引擎？
      - 不同数据源可能连接不同的数据库实例（不同主机、不同端口）
      - SQLAlchemy 的引擎绑定了一个固定的数据库 URL，不能动态切换
      - 独立引擎意味着独立连接池，互不影响

    线程安全：
      - _pools 是类变量（类级别共享），所有实例共用同一个字典
      - 在异步环境中，只要不在并发中同时创建/销毁同一个数据源的池，就是安全的
      - 如果需要更严格的并发控制，可以加 asyncio.Lock
    """

    _pools: dict[str, AsyncEngine] = {}  # 数据源 ID → 异步引擎的映射

    async def get_pool(self, ds: DataSource) -> AsyncEngine:
        """获取数据源的连接池（引擎），不存在则创建。

        参数：
            ds: DataSource 模型实例

        返回值：
            AsyncEngine — 该数据源的异步引擎

        工作流程：
            1. 检查缓存中是否已有该数据源的引擎
            2. 有 → 直接返回
            3. 没有 → 构建连接 URL → 创建引擎 → 缓存 → 返回
        """
        ds_id = str(ds.id)
        if ds_id in self._pools:
            return self._pools[ds_id]

        url = _build_url(ds)

        # 读取连接池配置，使用 getattr 做兼容性处理
        # getattr(settings, 'pool_min_size', settings.db_pool_size) 的含义：
        #   如果 settings 有 pool_min_size 属性就用它，否则用 db_pool_size
        # 这是为了兼容不同版本的配置（配置字段可能改名）
        pool_size = getattr(settings, 'pool_min_size', settings.db_pool_size)
        max_overflow = getattr(settings, 'pool_max_size', settings.db_pool_max_overflow)
        pool_timeout = getattr(settings, 'pool_timeout', settings.db_pool_timeout)
        pool_recycle = getattr(settings, 'pool_recycle', settings.db_pool_recycle)

        engine = create_async_engine(
            url.render_as_string(hide_password=False),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            # pool_pre_ping disabled: causes MissingGreenint with aiosqlite
            # pool_pre_ping=True 会在每次从池中取连接时发一个 SELECT 1 检测连接是否有效
            # 但 aiosqlite 驱动有 bug，开启后报 MissingGreenlet 错误，所以禁用
        )
        self._pools[ds_id] = engine
        logger.info(
            "Created connection pool for datasource %s (%s, type=%s, pool_size=%d, max_overflow=%d, recycle=%ds)",
            ds.name, ds_id, ds.db_type, pool_size, max_overflow, pool_recycle,
        )
        return engine

    async def refresh_pool(self, ds: DataSource) -> AsyncEngine:
        """刷新数据源的连接池 — 销毁旧的，创建新的。

        使用场景：用户修改了数据源的连接配置（如密码变更、端口变更），
        旧引擎的连接参数已失效，必须重建。

        参数：
            ds: DataSource 模型实例（包含最新的连接配置）

        返回值：
            AsyncEngine — 新创建的异步引擎
        """
        ds_id = str(ds.id)
        await self.close_pool(ds_id)
        return await self.get_pool(ds)

    async def close_pool(self, ds_id: str) -> None:
        """关闭指定数据源的连接池。

        engine.dispose() 会关闭池中所有连接，并清理池的资源。
        之后该引擎不能再使用，必须重新创建。

        参数：
            ds_id: 数据源 ID（字符串格式）
        """
        # dict.pop(key, default) — 移除并返回键对应的值
        # 如果键不存在，返回 None（不会抛 KeyError）
        engine = self._pools.pop(ds_id, None)
        if engine:
            await engine.dispose()
            logger.info(f"Closed connection pool for {ds_id}")

    async def close_all(self) -> None:
        """关闭所有数据源的连接池。

        通常在应用关闭时调用（如 FastAPI 的 shutdown 事件），
        确保所有数据库连接被正确关闭，避免资源泄漏。

        注意：遍历 dict 时不能修改 dict，所以先 list() 复制键。
        """
        for ds_id, engine in list(self._pools.items()):
            await engine.dispose()
            logger.info(f"Disposed pool for {ds_id}")
        self._pools.clear()

    def has_pool(self, ds_id: str) -> bool:
        """检查指定数据源是否已有连接池。

        参数：
            ds_id: 数据源 ID

        返回值：
            bool — True 表示已有连接池
        """
        return ds_id in self._pools

    async def get_pool_by_id(self, ds_id: str) -> AsyncEngine | None:
        """通过数据源 ID 获取已有的连接池，不创建新的。

        与 get_pool() 的区别：如果池不存在，返回 None 而不是创建。
        适用于只需要检查/使用已有池的场景。

        参数：
            ds_id: 数据源 ID

        返回值：
            AsyncEngine | None — 引擎实例或 None
        """
        return self._pools.get(ds_id)

    async def get_pool_status(self) -> list[dict]:
        """获取所有活跃连接池的状态信息。

        返回值示例：
        [
            {
                "datasource_id": "abc-123",
                "size": 10,           # 池中连接总数
                "checked_in": 8,      # 空闲可用的连接数
                "checked_out": 2,     # 正在使用的连接数
                "overflow": 0,        # 超出 pool_size 的临时连接数
                "invalid": 0          # 已失效的连接数
            },
            ...
        ]

        返回值：
            list[dict] — 每个活跃池的状态字典列表
        """
        status_list = []
        for ds_id, engine in self._pools.items():
            try:
                # engine.pool 是底层的同步连接池对象（AsyncEngine 的内部实现）
                pool = engine.pool
                status_list.append({
                    "datasource_id": ds_id,
                    "size": pool.size(),
                    "checked_in": pool.checkedin(),
                    "checked_out": pool.checkedout(),
                    "overflow": pool.overflow(),
                    "invalid": getattr(pool, 'invalidated', 0),
                })
            except Exception as e:
                status_list.append({
                    "datasource_id": ds_id,
                    "error": str(e),
                })
        logger.info("Pool status: %d active pools", len(status_list))
        return status_list

    async def health_check(self, ds_id: str, ds: DataSource) -> dict:
        """对指定数据源执行健康检查。

        通过执行 SELECT 1 检测数据库是否可达。
        如果连接失败，返回错误信息（中文，面向用户）。

        参数：
            ds_id: 数据源 ID
            ds: DataSource 模型实例

        返回值：
            dict — {"healthy": True/False, "error": 错误信息或 None}
        """
        try:
            engine = await self.get_pool(ds)
            async with engine.connect() as conn:
                # text("SELECT 1") — 执行最简单的查询，验证连接是否有效
                await conn.execute(text("SELECT 1"))
            return {"healthy": True, "error": None}
        except Exception as e:
            return {"healthy": False, "error": "连接失败"}


# 全局唯一的连接池管理器实例 — 整个应用共享
pool_manager = ConnectionPoolManager()
