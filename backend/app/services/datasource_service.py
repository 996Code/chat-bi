import uuid
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.encryption import encrypt_value, decrypt_value
from app.core.logging import get_logger
from app.db.models import DataSource
from app.schemas.datasource import DataSourceCreate, DataSourceUpdate

logger = get_logger(__name__)


class DataSourceService:
    def __init__(self, db: AsyncSession, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

    async def create(self, data: DataSourceCreate) -> DataSource:
        ds = DataSource(
            tenant_id=uuid.UUID(self.tenant_id),
            name=data.name,
            db_type=data.type,
            host=data.host,
            port=data.port,
            database_name=data.database_name,
            username_encrypted=encrypt_value(data.username),
            password_encrypted=encrypt_value(data.password),
            is_active=True,
        )
        self.db.add(ds)
        await self.db.commit()
        await self.db.refresh(ds)
        logger.info(f"Created datasource {ds.name} for tenant {self.tenant_id}")
        return ds

    async def get_by_id(self, ds_id: str) -> DataSource | None:
        result = await self.db.execute(
            select(DataSource).where(
                DataSource.id == uuid.UUID(ds_id),
                DataSource.tenant_id == uuid.UUID(self.tenant_id),
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[DataSource]:
        result = await self.db.execute(
            select(DataSource)
            .where(DataSource.tenant_id == uuid.UUID(self.tenant_id))
            .order_by(DataSource.created_at.desc())
        )
        return list(result.scalars().all())

    async def update(self, ds_id: str, data: DataSourceUpdate) -> DataSource | None:
        ds = await self.get_by_id(ds_id)
        if not ds:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if field in ("username", "password") and value is not None:
                setattr(ds, f"{field}_encrypted", encrypt_value(value))
            elif hasattr(ds, field):
                setattr(ds, field, value)

        await self.db.commit()
        await self.db.refresh(ds)
        return ds

    async def delete(self, ds_id: str) -> bool:
        ds = await self.get_by_id(ds_id)
        if not ds:
            return False
        await self.db.delete(ds)
        await self.db.commit()
        return True

    async def test_connection(self, ds_id: str) -> dict:
        ds = await self.get_by_id(ds_id)
        if not ds:
            return {"success": False, "error": "数据源不存在"}

        try:
            username = decrypt_value(ds.username_encrypted)
            password = decrypt_value(ds.password_encrypted)
            url = self._build_connection_url(ds, username, password)
            engine = create_async_engine(url, pool_pre_ping=True)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                # Get database list
                if ds.db_type == "postgresql":
                    result = await conn.execute(
                        text("SELECT datname FROM pg_database WHERE datistemplate = false")
                    )
                else:
                    result = await conn.execute(text("SHOW DATABASES"))
                databases = [row[0] for row in result.fetchall()]
            await engine.dispose()

            # Update health check
            ds.last_health_check = datetime.now(timezone.utc)
            ds.is_active = True
            await self.db.commit()

            return {"success": True, "message": "连接成功", "databases": databases}
        except Exception as e:
            ds.is_active = False
            await self.db.commit()
            return {"success": False, "error": str(e)}

    @staticmethod
    def _build_connection_url(ds: DataSource, username: str, password: str) -> str:
        if ds.db_type == "postgresql":
            return (
                f"postgresql+asyncpg://{username}:{password}"
                f"@{ds.host}:{ds.port}/{ds.database_name}"
            )
        # Default: MySQL
        return (
            f"mysql+aiomysql://{username}:{password}"
            f"@{ds.host}:{ds.port}/{ds.database_name}"
            f"?charset=utf8mb4"
        )
