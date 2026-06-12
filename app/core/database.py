from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


print("DATABASE_URL =", settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,          # don't let this grow unbounded
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,    # recycle connections every 30 min
    pool_pre_ping=True,   # detect stale connections
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Called on startup to create all tables."""
    async with engine.begin() as conn:
        from app.models import User, notebook, document  # noqa: F401 - register models
        await conn.run_sync(Base.metadata.create_all)
