"""PostgreSQL implementation of :class:`app.db.repository.Repository`.

Imported lazily (see :func:`app.db.build_repository`) so that offline mode never
requires a database driver to be installed, let alone reachable.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ConversationSession, Feedback, Message, SafetyLog
from app.db.repository import (
    FeedbackRecord,
    MessageRecord,
    Repository,
    SafetyLogRecord,
    SessionRecord,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _describe_bind(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """``postgresql+asyncpg`` / ``sqlite+aiosqlite`` - read from the live engine.

    The label used to be the hardcoded string ``"sqlalchemy"``, which is equally
    true of a PostgreSQL and a SQLite deployment. ``/health`` therefore could not
    answer the one question it was being asked - *which database am I actually
    talking to* - so the dialect is now read from the bound engine's URL rather
    than asserted.
    """
    bind = session_factory.kw.get("bind")
    url = getattr(bind, "url", None)
    if url is None:  # pragma: no cover - a session factory with no bind
        return "sqlalchemy(unbound)"
    driver = url.get_driver_name()
    return f"{url.get_backend_name()}+{driver}" if driver else url.get_backend_name()


class SqlAlchemyRepository(Repository):
    """Async PostgreSQL implementation (also works against SQLite in tests)."""

    #: Overwritten per instance in __init__ with the dialect actually connected.
    backend = "sqlalchemy"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.backend = _describe_bind(session_factory)

    # ------------------------------------------------------------ mapping
    @staticmethod
    def _to_session_record(row: ConversationSession) -> SessionRecord:
        return SessionRecord(
            id=row.id,
            title=row.title,
            locale=row.locale,
            rolling_summary=row.rolling_summary or "",
            turn_count=row.turn_count,
            consent_version=row.consent_version,
            consent_at=row.consent_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_message_record(row: Message) -> MessageRecord:
        return MessageRecord(
            id=row.id,
            session_id=row.session_id,
            role=row.role,
            content=row.content,
            emotion=row.emotion,
            intent=row.intent,
            safety=row.safety,
            retrieval=row.retrieval,
            generation=row.generation,
            memory=row.memory,
            timings=row.timings,
            citations=list(row.citations or []),
            crisis=row.crisis,
            latency_ms=row.latency_ms,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_safety_record(row: SafetyLog) -> SafetyLogRecord:
        return SafetyLogRecord(
            id=row.id,
            session_id=row.session_id,
            message_id=row.message_id,
            stage=row.stage,
            blocked=row.blocked,
            layers=list(row.layers or []),
            categories=list(row.categories or []),
            triggers=dict(row.triggers or {}),
            excerpt=row.excerpt,
            dominant_emotion=row.dominant_emotion,
            emotion_intensity=row.emotion_intensity,
            intent=row.intent,
            created_at=row.created_at,
        )

    # ----------------------------------------------------------- sessions
    async def create_session(self, locale: str = "en-IN", title: str = "") -> SessionRecord:
        async with self.session_factory() as session:
            row = ConversationSession(id=str(uuid.uuid4()), locale=locale, title=title)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._to_session_record(row)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with self.session_factory() as session:
            row = await session.get(ConversationSession, session_id)
            return self._to_session_record(row) if row else None

    async def ensure_session(self, session_id: str | None, locale: str = "en-IN") -> SessionRecord:
        if session_id:
            existing = await self.get_session(session_id)
            if existing:
                return existing
            async with self.session_factory() as session:
                row = ConversationSession(id=session_id, locale=locale)
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return self._to_session_record(row)
        return await self.create_session(locale=locale)

    async def record_consent(self, session_id: str, version: str) -> SessionRecord:
        await self.ensure_session(session_id)
        async with self.session_factory() as session:
            row = await session.get(ConversationSession, session_id)
            if row is None:  # pragma: no cover - ensure_session just created it
                raise RuntimeError(f"session {session_id!r} vanished during consent write")
            row.consent_version = version
            row.consent_at = _utcnow()
            row.updated_at = row.consent_at
            await session.commit()
            await session.refresh(row)
            return self._to_session_record(row)

    async def update_summary(self, session_id: str, summary: str) -> None:
        async with self.session_factory() as session:
            row = await session.get(ConversationSession, session_id)
            if row is None:
                return
            row.rolling_summary = summary
            row.updated_at = _utcnow()
            await session.commit()

    # ----------------------------------------------------------- feedback
    @staticmethod
    def _to_feedback_record(row: Feedback) -> FeedbackRecord:
        return FeedbackRecord(
            id=row.id,
            message_id=row.message_id,
            session_id=row.session_id,
            rating=row.rating,
            comment=row.comment or "",
            created_at=row.created_at,
        )

    async def upsert_feedback(self, record: FeedbackRecord) -> FeedbackRecord:
        """Replace this message's verdict, so one turn has at most one row.

        A tester who changes their mind, or adds a comment after voting, should
        leave one unambiguous verdict rather than a stack of them.
        """
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Feedback).where(Feedback.message_id == record.message_id)
            )
            if row is None:
                row = Feedback(
                    id=record.id or str(uuid.uuid4()),
                    message_id=record.message_id,
                    session_id=record.session_id,
                )
                session.add(row)
            row.rating = record.rating
            row.comment = record.comment
            row.updated_at = _utcnow()
            await session.commit()
            await session.refresh(row)
            return self._to_feedback_record(row)

    async def get_feedback(self, message_id: str) -> FeedbackRecord | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Feedback).where(Feedback.message_id == message_id)
            )
            return self._to_feedback_record(row) if row else None

    # ----------------------------------------------------------- messages
    async def add_message(self, message: MessageRecord) -> MessageRecord:
        async with self.session_factory() as session:
            row = Message(
                id=message.id or str(uuid.uuid4()),
                session_id=message.session_id,
                role=message.role,
                content=message.content,
                emotion=message.emotion,
                intent=message.intent,
                safety=message.safety,
                retrieval=message.retrieval,
                generation=message.generation,
                memory=message.memory,
                timings=message.timings,
                citations=message.citations,
                crisis=message.crisis,
                latency_ms=message.latency_ms,
            )
            session.add(row)
            parent = await session.get(ConversationSession, message.session_id)
            if parent is not None:
                parent.turn_count += 1
                parent.updated_at = _utcnow()
                if not parent.title and message.role == "user":
                    parent.title = message.content[:80]
            await session.commit()
            await session.refresh(row)
            return self._to_message_record(row)

    async def get_message(self, message_id: str) -> MessageRecord | None:
        async with self.session_factory() as session:
            row = await session.get(Message, message_id)
            return self._to_message_record(row) if row else None

    async def list_messages(
        self, session_id: str, limit: int = 20, newest_first: bool = False
    ) -> list[MessageRecord]:
        async with self.session_factory() as session:
            order = desc(Message.created_at) if newest_first else Message.created_at
            statement = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(order)
                .limit(limit)
            )
            rows: Sequence[Message] = (await session.execute(statement)).scalars().all()
            records = [self._to_message_record(row) for row in rows]
            return records

    # ------------------------------------------------------------- safety
    async def log_safety(self, record: SafetyLogRecord) -> SafetyLogRecord:
        async with self.session_factory() as session:
            row = SafetyLog(
                id=record.id or str(uuid.uuid4()),
                session_id=record.session_id,
                message_id=record.message_id,
                stage=record.stage,
                blocked=record.blocked,
                layers=record.layers,
                categories=record.categories,
                triggers=record.triggers,
                excerpt=record.excerpt,
                dominant_emotion=record.dominant_emotion,
                emotion_intensity=record.emotion_intensity,
                intent=record.intent,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._to_safety_record(row)

    async def list_safety_logs(self, limit: int = 50) -> list[SafetyLogRecord]:
        async with self.session_factory() as session:
            statement = select(SafetyLog).order_by(desc(SafetyLog.created_at)).limit(limit)
            rows: Sequence[SafetyLog] = (await session.execute(statement)).scalars().all()
            return [self._to_safety_record(row) for row in rows]

    async def health(self) -> bool:
        try:
            async with self.session_factory() as session:
                await session.execute(select(1))
            return True
        except Exception:  # pragma: no cover - needs a live database
            logger.exception("database health check failed")
            return False
