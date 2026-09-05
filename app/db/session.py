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


#: Columns added after the first schema went live, as ``(table, column, type)``.
#:
#: ``create_all`` creates missing *tables* but never adds a column to a table
#: that already exists, so a database created before one of these entries would
#: silently reject every insert that mentions it. Each entry is applied with
#: ``ADD COLUMN IF NOT EXISTS``: additive only, idempotent, and it never drops
#: or retypes anything. A production deployment would use Alembic; this is the
#: honest minimum for a research project that already has live data.
ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Day 2: memory observability (app.pipeline.schemas.MemoryTrace).
    ("messages", "memory", "JSON"),
    # Day 3: per-stage latency instrumentation.
    ("messages", "timings", "JSON"),
    # Day 5: consent recording.
    ("sessions", "consent_version", "VARCHAR(32)"),
    ("sessions", "consent_at", "TIMESTAMP WITH TIME ZONE"),
)


async def init_models(engine: AsyncEngine) -> None:
    """Create tables if absent, then add any columns a live table is missing.

    Fine for a research project; a production deployment would use Alembic.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    if engine.dialect.name != "postgresql":
        logger.info("database schema ensured (%s)", engine.dialect.name)
        return

    from sqlalchemy import text

    async with engine.begin() as connection:
        for table, column, column_type in ADDITIVE_COLUMNS:
            await connection.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {column_type}')
            )
    logger.info("database schema ensured (%d additive column(s) checked)", len(ADDITIVE_COLUMNS))


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
