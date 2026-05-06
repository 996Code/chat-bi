"""Multi-dialect connection pool manager with safe URL construction."""
import urllib.parse
import uuid
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy import URL, text

from app.core.logging import get_logger
from app.core.encryption import decrypt_value
from app.db.models import DataSource

logger = get_logger(__name__)


def _build_url(ds: DataSource) -> URL:
    """Build SQLAlchemy URL safely using URL.create() with quoted credentials."""
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
        return URL.create("sqlite+aiosqlite", database=ds.database_name)
    else:
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
    _pools: dict[str, AsyncEngine] = {}

    async def get_pool(self, ds: DataSource) -> AsyncEngine:
        ds_id = str(ds.id)
        if ds_id in self._pools:
            return self._pools[ds_id]

        url = _build_url(ds)
        engine = create_async_engine(
            url.render_as_string(hide_password=False),
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=3600,
            # pool_pre_ping disabled: causes MissingGreenint with aiosqlite
        )
        self._pools[ds_id] = engine
        logger.info(f"Created connection pool for datasource {ds.name} ({ds_id}, type={ds.db_type})")
        return engine

    async def refresh_pool(self, ds: DataSource) -> AsyncEngine:
        """Dispose old pool and create a new one (e.g., after credential change)."""
        ds_id = str(ds.id)
        await self.close_pool(ds_id)
        return await self.get_pool(ds)

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
        return ds_id in self._pools

    async def get_pool_by_id(self, ds_id: str) -> AsyncEngine | None:
        return self._pools.get(ds_id)

    async def health_check(self, ds_id: str, ds: DataSource) -> dict:
        try:
            engine = await self.get_pool(ds)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"healthy": True, "error": None}
        except Exception as e:
            return {"healthy": False, "error": "连接失败"}


pool_manager = ConnectionPoolManager()
