"""Persistence behind one interface, with a real and a fake implementation.

Research-pipeline role
---------------------
The orchestrator and :mod:`app.pipeline.memory` only ever see
:class:`Repository`. ``InMemoryRepository`` is a complete implementation (not a
stub), so the entire chat flow - sessions, rolling summary, safety logs and
``GET /explain/{message_id}`` - works with no PostgreSQL instance running.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Transport records (kept ORM-free so the API layer never touches SQLAlchemy)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SessionRecord:
    id: str
    title: str = ""
    locale: str = "en-IN"
    rolling_summary: str = ""
    turn_count: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(slots=True)
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    emotion: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    #: Serialised :class:`app.pipeline.schemas.MemoryTrace` for this turn.
    memory: dict[str, Any] | None = None
    citations: list[str] = field(default_factory=list)
    crisis: bool = False
    latency_ms: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(slots=True)
class SafetyLogRecord:
    id: str
    session_id: str | None
    message_id: str | None
    stage: str
    blocked: bool
    layers: list[str]
    categories: list[str]
    triggers: dict[str, Any]
    excerpt: str
    dominant_emotion: str = "neutral"
    emotion_intensity: float = 0.0
    intent: str = "other"
    created_at: datetime = field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class Repository(ABC):
    """Everything the pipeline needs to persist or read back."""

    backend: str = "abstract"

    # sessions ------------------------------------------------------------
    @abstractmethod
    async def create_session(self, locale: str = "en-IN", title: str = "") -> SessionRecord: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord | None: ...

    @abstractmethod
    async def ensure_session(self, session_id: str | None, locale: str = "en-IN") -> SessionRecord:
        """Fetch the session, creating it when the id is unknown or ``None``."""

    @abstractmethod
    async def update_summary(self, session_id: str, summary: str) -> None: ...

    # messages ------------------------------------------------------------
    @abstractmethod
    async def add_message(self, message: MessageRecord) -> MessageRecord: ...

    @abstractmethod
    async def get_message(self, message_id: str) -> MessageRecord | None: ...

    @abstractmethod
    async def list_messages(
        self, session_id: str, limit: int = 20, newest_first: bool = False
    ) -> list[MessageRecord]: ...

    # safety --------------------------------------------------------------
    @abstractmethod
    async def log_safety(self, record: SafetyLogRecord) -> SafetyLogRecord: ...

    @abstractmethod
    async def list_safety_logs(self, limit: int = 50) -> list[SafetyLogRecord]: ...

    async def aclose(self) -> None:
        return None

    async def health(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# Offline implementation
# --------------------------------------------------------------------------- #
class InMemoryRepository(Repository):
    """Dict-backed repository used in mock mode and in every unit test."""

    backend = "in-memory"

    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}
        self.messages: dict[str, MessageRecord] = {}
        self.safety_logs: list[SafetyLogRecord] = []

    async def create_session(self, locale: str = "en-IN", title: str = "") -> SessionRecord:
        record = SessionRecord(id=str(uuid.uuid4()), locale=locale, title=title)
        self.sessions[record.id] = record
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    async def ensure_session(self, session_id: str | None, locale: str = "en-IN") -> SessionRecord:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        if session_id:
            # Honour a client-supplied id so reloads keep their conversation.
            record = SessionRecord(id=session_id, locale=locale)
            self.sessions[session_id] = record
            return record
        return await self.create_session(locale=locale)

    async def update_summary(self, session_id: str, summary: str) -> None:
        session = self.sessions.get(session_id)
        if session is not None:
            session.rolling_summary = summary
            session.updated_at = _utcnow()

    async def add_message(self, message: MessageRecord) -> MessageRecord:
        if not message.id:
            message.id = str(uuid.uuid4())
        self.messages[message.id] = message
        session = self.sessions.get(message.session_id)
        if session is not None:
            session.turn_count += 1
            session.updated_at = _utcnow()
            if not session.title and message.role == "user":
                session.title = message.content[:80]
        return message

    async def get_message(self, message_id: str) -> MessageRecord | None:
        return self.messages.get(message_id)

    async def list_messages(
        self, session_id: str, limit: int = 20, newest_first: bool = False
    ) -> list[MessageRecord]:
        items = [item for item in self.messages.values() if item.session_id == session_id]
        items.sort(key=lambda item: item.created_at, reverse=newest_first)
        if newest_first:
            return items[:limit]
        return items[-limit:]

    async def log_safety(self, record: SafetyLogRecord) -> SafetyLogRecord:
        if not record.id:
            record.id = str(uuid.uuid4())
        self.safety_logs.append(record)
        return record

    async def list_safety_logs(self, limit: int = 50) -> list[SafetyLogRecord]:
        return self.safety_logs[-limit:]
