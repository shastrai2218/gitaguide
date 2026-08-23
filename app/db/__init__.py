"""Persistence layer.

``repository`` (interface + records + in-memory fake) is dependency-free on
purpose: importing :mod:`app.db` must not require SQLAlchemy or a driver, so
offline development and unit tests need nothing installed beyond the core.
The SQLAlchemy models and repository are imported lazily by
:func:`build_repository` / module ``__getattr__``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config import Settings, get_settings
from app.db.repository import (
    InMemoryRepository,
    MessageRecord,
    Repository,
    SafetyLogRecord,
    SessionRecord,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.db.models import Base, ConversationSession, Message, SafetyLog
    from app.db.sql_repository import SqlAlchemyRepository

logger = logging.getLogger(__name__)

__all__ = [
    "Base",
    "ConversationSession",
    "InMemoryRepository",
    "Message",
    "MessageRecord",
    "Repository",
    "SafetyLog",
    "SafetyLogRecord",
    "SessionRecord",
    "SqlAlchemyRepository",
    "build_repository",
]

_LAZY: dict[str, tuple[str, str]] = {
    "Base": ("app.db.models", "Base"),
    "ConversationSession": ("app.db.models", "ConversationSession"),
    "Message": ("app.db.models", "Message"),
    "SafetyLog": ("app.db.models", "SafetyLog"),
    "SqlAlchemyRepository": ("app.db.sql_repository", "SqlAlchemyRepository"),
}


def __getattr__(name: str) -> Any:  # pragma: no cover - import plumbing
    """Resolve SQLAlchemy-backed names only when they are actually used."""
    if name in _LAZY:
        import importlib

        module_name, attribute = _LAZY[name]
        return getattr(importlib.import_module(module_name), attribute)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def build_repository(settings: Settings | None = None) -> Repository:
    """Return the configured repository, creating tables when using Postgres.

    Falls back to :class:`InMemoryRepository` if the database cannot be reached,
    so a missing container degrades the app instead of killing it.
    """
    settings = settings or get_settings()
    if not settings.postgres_enabled:
        logger.info("using in-memory repository (mock mode)")
        return InMemoryRepository()

    try:
        from app.db.session import create_engine, create_session_factory, init_models
        from app.db.sql_repository import SqlAlchemyRepository

        engine = create_engine(settings)
        await init_models(engine)
        repository = SqlAlchemyRepository(create_session_factory(engine))
        logger.info("using PostgreSQL repository")
        return repository
    except Exception:  # pragma: no cover - depends on a live database
        logger.exception("database unavailable; falling back to in-memory repository")
        return InMemoryRepository()
