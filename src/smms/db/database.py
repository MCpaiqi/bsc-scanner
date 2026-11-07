from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from smms.config.settings import Settings, get_settings

_engine: AsyncEngine | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        active = settings or get_settings()
        _engine = create_async_engine(active.database_url, pool_pre_ping=True)
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


@asynccontextmanager
async def session_scope(settings: Settings | None = None) -> AsyncIterator:
    async with get_session_factory(settings)() as session:
        yield session


async def check_database(settings: Settings | None = None) -> str:
    try:
        async with get_engine(settings).connect() as connection:
            await connection.execute(text("SELECT 1"))
        return "ok"
    except (ModuleNotFoundError, OSError, SQLAlchemyError, PostgresError):
        return "unavailable"


async def dispose_database() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
