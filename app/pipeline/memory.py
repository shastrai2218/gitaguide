"""Stage 6 - conversation memory (rolling summary + last N turns).

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
from app.pipeline.textutils import truncate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryContext:
    """What the generator is allowed to know about earlier turns."""

    session_id: str
    summary: str = ""
    recent_turns: list[tuple[str, str]] = field(default_factory=list)
    turn_count: int = 0

    def render(self, max_chars: int = 2000) -> str:
        """Prompt-ready block; empty string when there is no history."""
        blocks: list[str] = []
        if self.summary:
            blocks.append(f"CONVERSATION SUMMARY SO FAR:\n{self.summary}")
        if self.recent_turns:
            lines = [f"{role}: {content}" for role, content in self.recent_turns]
            blocks.append("RECENT TURNS:\n" + "\n".join(lines))
        rendered = "\n\n".join(blocks)
        return truncate(rendered, max_chars) if rendered else ""

    @property
    def is_empty(self) -> bool:
        return not self.summary and not self.recent_turns


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
            (record.role, truncate(record.content, 400))
            for record in messages
            if record.role in {"user", "assistant"}
        ]
        return MemoryContext(
            session_id=session_id,
            summary=summary,
            recent_turns=recent,
            turn_count=turn_count,
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
