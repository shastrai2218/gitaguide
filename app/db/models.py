"""SQLAlchemy models for sessions, messages and safety logs.

Research-pipeline role
---------------------
Two reasons this exists beyond "the app needs a database":

1. **Memory** - :mod:`app.pipeline.memory` reads the last N turns and the
   rolling summary from here and injects them into the generation prompt.
2. **Auditability** - every safety-gate trigger is written to ``safety_logs``
   with the layer, category and the emotion/intent readings at that moment, so
   gate precision/recall can be reported on real traffic rather than only on the
   red-team suite.

The full JSON pipeline trace is stored on the message row, which is what
``GET /explain/{message_id}`` serves.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base; JSON columns are portable across SQLite and Postgres."""

    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class ConversationSession(Base):
    """One chat session (the table is ``sessions``)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str] = mapped_column(String(200), default="")
    locale: Mapped[str] = mapped_column(String(16), default="en-IN")
    rolling_summary: Mapped[str] = mapped_column(Text, default="")
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Which version of the consent text this session agreed to, and when. Null
    #: on rows written before consent recording existed, and on any session that
    #: has not consented - which the chat endpoint refuses to serve.
    consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """One user or assistant turn, with the pipeline trace attached."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)

    #: Serialised pipeline stages (``EmotionResult``, ``IntentResult``, ...).
    emotion: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    safety: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retrieval: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    generation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: What the memory stage injected into the prompt for this turn. Null on
    #: rows written before memory observability existed.
    memory: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: Per-stage wall clock in milliseconds. Null on rows written before
    #: latency instrumentation existed.
    timings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    citations: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    crisis: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[ConversationSession] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)


class SafetyLog(Base):
    """One safety-gate trigger (input or output layer)."""

    __tablename__ = "safety_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    stage: Mapped[str] = mapped_column(String(16), default="input")  # input | output
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    layers: Mapped[list[str]] = mapped_column(JSON, default=list)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    triggers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    excerpt: Mapped[str] = mapped_column(Text, default="")
    dominant_emotion: Mapped[str] = mapped_column(String(32), default="neutral")
    emotion_intensity: Mapped[float] = mapped_column(Float, default=0.0)
    intent: Mapped[str] = mapped_column(String(32), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
