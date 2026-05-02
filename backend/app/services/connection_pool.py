import uuid
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import text

from app.core.logging import get_logger
from app.core.encryption import decrypt_value
from app.db.models import DataSource

logger = get_logger(__name__)


class ConnectionPoolManager:
    _pools: dict[str, AsyncEngine] = {}

    async def get_pool(self, ds: DataSource) -> AsyncEngine:
        ds_id = str(ds.id)
        if ds_id in self._pools:
            return self._pools[ds_id]

        username = decrypt_value(ds.username_encrypted)
        password = decrypt_value(ds.password_encrypted)

        if ds.db_type == "postgresql":
            url = (
                f"postgresql+asyncpg://{username}:{password}"
                f"@{ds.host}:{ds.port}/{ds.database_name}"
            )
        elif ds.db_type == "sqlite":
            url = f"sqlite+aiosqlite:///{ds.database_name}"
        else:
            # Default to MySQL
            url = (
                f"mysql+aiomysql://{username}:{password}"
                f"@{ds.host}:{ds.port}/{ds.database_name}"
                f"?charset=utf8mb4&read_only=on"
            )

        engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,
        )
        self._pools[ds_id] = engine
        logger.info(f"Created connection pool for datasource {ds.name} ({ds_id}, type={ds.db_type})")
        return engine

    async def close_pool(self, ds_id: str) -> None:
        engine = self._pools.pop(ds_id, None)
        if engine:
            await engine.dispose()
            logger.info(f"Closed connection pool for {ds_id}")

    async def close_all(self) -> None:
        for ds_id, engine in list(self._pools.items()):
            await engine.dispose()
            logger.info(f"Disposed pool for {ds_id}")
        self._pools.clear()

    def has_pool(self, ds_id: str) -> bool:
        """公开方法检查连接池是否存在，避免访问私有属性。"""
        return ds_id in self._pools

    async def get_pool_by_id(self, ds_id: str) -> AsyncEngine | None:
        """公开方法获取连接池。"""
        return self._pools.get(ds_id)

    async def health_check(self, ds_id: str, ds: DataSource) -> dict:
        try:
            engine = await self.get_pool(ds)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"healthy": True, "error": None}
        except Exception as e:
            return {"healthy": False, "error": str(e)}


pool_manager = ConnectionPoolManager()
