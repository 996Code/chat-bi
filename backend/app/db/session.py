from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.config import settings

_pool_kwargs = {}
if not settings.database_url.startswith("sqlite"):
    _pool_kwargs = {
        "pool_size": settings.pool_min_size,
        "max_overflow": settings.pool_max_size - settings.pool_min_size,
        "pool_timeout": settings.pool_timeout,
        "pool_recycle": settings.pool_recycle,
        "pool_pre_ping": True,
    }

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    **_pool_kwargs,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with async_session_factory() as session:
        yield session
