"""Async SQLAlchemy engine/session plumbing.

Only used when ``postgres_enabled`` is true; in mock mode the app runs on
:class:`app.db.repository.InMemoryRepository` and never imports a driver.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.db.models import Base

logger = logging.getLogger(__name__)


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create the async engine for the configured DSN."""
    settings = settings or get_settings()
    return create_async_engine(
        settings.postgres_dsn,
        echo=settings.environment == "dev" and settings.log_level == "DEBUG",
        pool_pre_ping=True,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_models(engine: AsyncEngine) -> None:
    """Create tables if absent.

    Fine for a research project; a production deployment would use Alembic.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    logger.info("database schema ensured")


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Async context helper that commits on success and rolls back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
