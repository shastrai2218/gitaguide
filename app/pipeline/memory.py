"""Stage 6 - conversation memory (rolling summary + last N turns).

Called by: app.pipeline.orchestrator (stage 4) and generation.

Research-pipeline role
---------------------
Guidance is a multi-turn activity: "should I take the offer?" three turns later
means nothing without the earlier context. Rather than replaying an unbounded
transcript into the prompt, GitaGuide keeps

* the **last N turns** verbatim (default 6, ``memory_last_n_turns``), and
* a **rolling summary** of everything older, refreshed by the LLM every
  ``memory_summary_trigger_turns`` turns and stored on the session row.

Both are injected into the generation prompt; the summary is also returned in
the API response so the explanation panel can show what the model "remembered".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.db.repository import MessageRecord, Repository
from app.llm.base import TASK_SUMMARY, LLMProvider
from app.pipeline.schemas import MemoryTrace
from app.pipeline.textutils import WHY_HEADING_RE, truncate

logger = logging.getLogger(__name__)


def strip_verse_payload(text: str) -> str:
    """Remove a "Why this verse" section from a turn being replayed as history.

    History is the one place where an old reply's shape outlives the prompt that
    produced it. Replies written before the Phase 1 response contract carry a
    40-170 word explanation section and, in the oldest ones, pasted verse text -
    and a session that started then keeps feeding both back into every new
    prompt. That is not a cosmetic problem: it re-teaches the model the format
    testers asked us to drop, and it spends a few hundred tokens per turn
    restating graph paths the model cannot use.

    Verse **ids** are deliberately kept. They are the thread of the
    conversation: "explain that verse more" is only answerable because BG-2.47
    is still visible in the history.

    >>> strip_verse_payload("Do the work (BG-2.47).\\n### Why this verse\\n- BG-2.47: x")
    'Do the work (BG-2.47).'
    """
    match = WHY_HEADING_RE.search(text)
    body = text[: match.start()] if match else text
    return body.strip() or text.strip()


@dataclass(slots=True)
class MemoryContext:
    """What the generator is allowed to know about earlier turns."""

    session_id: str
    summary: str = ""
    recent_turns: list[tuple[str, str]] = field(default_factory=list)
    turn_count: int = 0
    #: Verse ids already cited in this session, oldest first, de-duplicated.
    #: A follow-up prefers these: "that verse" means the one they were just
    #: shown, not whichever one scores highest on a query of two words.
    cited_refs: list[str] = field(default_factory=list)

    def render(self, max_chars: int = 2000) -> str:
        """Prompt-ready block; empty string when there is no history."""
        blocks: list[str] = []
        if self.summary:
            blocks.append(f"CONVERSATION SUMMARY SO FAR:\n{self.summary}")
        if self.recent_turns:
            lines = [
                f"{role}: {strip_verse_payload(content)}" for role, content in self.recent_turns
            ]
            blocks.append("RECENT TURNS:\n" + "\n".join(lines))
        rendered = "\n\n".join(blocks)
        return truncate(rendered, max_chars) if rendered else ""

    def cited_verse_ids(self, limit: int = 4) -> list[str]:
        """The most recently cited verse ids, newest first."""
        return list(reversed(self.cited_refs))[:limit]

    @property
    def is_empty(self) -> bool:
        return not self.summary and not self.recent_turns

    def trace(self) -> MemoryTrace:
        """The observable record of what this context contributes to the prompt.

        Built here rather than by a caller so that every consumer - the API, the
        persisted message row, the evaluation harness - reads the same numbers
        from the object that was actually rendered into the prompt.
        """
        return MemoryTrace(
            turn_count=self.turn_count,
            summary_included=bool(self.summary),
            recent_turns_injected=len(self.recent_turns),
            loaded=True,
        )


class ConversationMemory:
    """Reads history from the repository and maintains the rolling summary."""

    def __init__(
        self,
        repository: Repository,
        llm: LLMProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository
        self.llm = llm
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ read
    async def load(self, session_id: str) -> MemoryContext:
        """Assemble the memory context for a session."""
        session = await self.repository.get_session(session_id)
        summary = session.rolling_summary if session else ""
        turn_count = session.turn_count if session else 0

        messages = await self.repository.list_messages(
            session_id, limit=self.settings.memory_last_n_turns
        )
        recent = [
            (record.role, truncate(strip_verse_payload(record.content), 400))
            for record in messages
            if record.role in {"user", "assistant"}
        ]

        # Oldest first, de-duplicated, so a verse cited three turns running
        # appears once and the order still says which came last.
        cited: list[str] = []
        for record in messages:
            for verse_id in record.citations or ():
                if verse_id not in cited:
                    cited.append(verse_id)

        return MemoryContext(
            session_id=session_id,
            summary=summary,
            recent_turns=recent,
            turn_count=turn_count,
            cited_refs=cited,
        )

    # ----------------------------------------------------------------- write
    async def maybe_update_summary(self, session_id: str) -> str:
        """Refresh the rolling summary when enough turns have accumulated.

        Returns the (possibly unchanged) summary. Failures are swallowed: losing
        a summary must never fail a chat request.
        """
        session = await self.repository.get_session(session_id)
        if session is None:
            return ""
        trigger = max(2, self.settings.memory_summary_trigger_turns)
        if session.turn_count < trigger or session.turn_count % trigger != 0:
            return session.rolling_summary

        history = await self.repository.list_messages(session_id, limit=50)
        if not history:
            return session.rolling_summary

        summary = await self._summarise(history, session.rolling_summary)
        if summary:
            await self.repository.update_summary(session_id, summary)
            logger.info("rolling summary refreshed for session %s", session_id)
            return summary
        return session.rolling_summary

    async def _summarise(self, history: list[MessageRecord], previous: str) -> str:
        """LLM-backed extractive/abstractive summary of the conversation."""
        if self.llm is None:
            return self._fallback_summary(history, previous)

        transcript = "\n".join(
            f"{record.role}: {truncate(record.content, 300)}" for record in history
        )
        prompt = (
            f"TASK: {TASK_SUMMARY}\n"
            "Summarise this guidance conversation for future context. Keep, in at most "
            "150 words: the person's situation, what they are deciding, the emotions they "
            "have expressed, any verses already discussed (by id), and any commitment they "
            "made. Write plainly, third person, no advice.\n\n"
            f"PREVIOUS SUMMARY: {previous or 'none'}\n\n"
            f"TRANSCRIPT:\n{transcript}\n"
        )
        try:
            response = await self.llm.complete(prompt, max_tokens=400, temperature=0.2)
            return truncate(response.text.strip(), self.settings.memory_summary_max_chars)
        except Exception:
            logger.exception("summary generation failed; using extractive fallback")
            return self._fallback_summary(history, previous)

    def _fallback_summary(self, history: list[MessageRecord], previous: str) -> str:
        """Deterministic fallback: the user's own words, oldest first."""
        user_turns = [record.content for record in history if record.role == "user"]
        if not user_turns:
            return previous
        joined = " / ".join(truncate(turn, 120) for turn in user_turns[-6:])
        return truncate(
            f"The person has raised: {joined}", self.settings.memory_summary_max_chars
        )
