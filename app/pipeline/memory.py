"""Stage 6 - conversation memory (rolling summary + last N turns + TopicContext).

Called by: app.pipeline.orchestrator (stage 4) and generation.

Research-pipeline role
---------------------
Guidance is a multi-turn activity: "should I take the offer?" three turns later
means nothing without the earlier context. Rather than replaying an unbounded
transcript into the prompt, GitaGuide keeps

* structured **TopicContext**: active topics, historical topics, user-stated facts,
  explicit user decisions vs assistant recommendations, and unanswered questions;
* the **last N turns** verbatim (default 6, ``memory_last_n_turns``), and
* a **rolling summary** of everything older, refreshed by the LLM every
  ``memory_summary_trigger_turns`` turns and stored on the session row.

Critical Invariants
-------------------
1. Never represent an assistant recommendation as a user decision.
2. Never merge unrelated topics (e.g. father's financial loan != relationship breakup).
3. If multiple topics are active, identify ambiguous turns ("I'm stressed") as multi-topic.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.db.repository import MessageRecord, Repository
from app.llm.base import TASK_SUMMARY, LLMProvider
from app.pipeline.schemas import (
    DecisionRecord,
    MemoryTrace,
    QuantitativeFact,
    QuestionStatus,
    TopicContext,
    TopicRecord,
    TopicStatus,
    _utcnow,
)
from app.pipeline.textutils import WHY_HEADING_RE, truncate

logger = logging.getLogger(__name__)


def strip_verse_payload(text: str) -> str:
    """Remove a "Why this verse" section from a turn being replayed as history."""
    match = WHY_HEADING_RE.search(text)
    body = text[: match.start()] if match else text
    return body.strip() or text.strip()


# Topic signature cues
TOPIC_CUES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "financial",
        "Financial & Debt",
        re.compile(
            r"\b(?:loan|crore|1 cr|lakh|debt|money|stuck|payment|emi|dues|bank|fraud|bankruptcy|salary|savings)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "relationship",
        "Relationship & Heartbreak",
        re.compile(
            r"\b(?:broke up|breakup|cheated|cheating|ex|girlfriend|boyfriend|fianc[eé]|wife|husband|divorce|infidelity|dating)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "career",
        "Career & Workplace",
        re.compile(
            r"\b(?:job|boss|manager|office|promotion|appraisal|fired|laid off|colleague|coworker|interview|startup|workplace)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "family",
        "Family & Relatives",
        re.compile(
            r"\b(?:father|mother|mom|dad|brother|sister|parents|in-laws|cousin|family|relative)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "academic",
        "Studies & Exams",
        re.compile(
            r"\b(?:exam|exams|test|upsc|gate|college|university|thesis|dissertation|school|marks|grades|backlog)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "grief_bereavement",
        "Grief & Loss",
        re.compile(
            r"\b(?:passed away|died|death|funeral|lost my|grieving|mourning)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "scripture_study",
        "Scripture & Philosophical Study",
        re.compile(
            r"\b(?:shloka|slok|verse|bhagavad|gita|arthashastra|kautilya|krishna|arjuna|chapter \d+|sanskrit)\b",
            re.IGNORECASE,
        ),
    ),
]

QUANTITATIVE_PATTERNS = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:cr|crore|crores|lakh|lakhs|lac|lacs|k|thousand|thousands|rupees|rs\.?|inr|months?|years?|days?|hours?|%|percent))\b|"
    r"\b(?:loan of [^,.\n]+|order of [^,.\n]+|debt of [^,.\n]+)\b",
    re.IGNORECASE,
)

DECISION_PATTERNS = re.compile(
    r"\b(?:i (?:have )?decided to|i will|i am going to|i have chosen to|i promise to|i made up my mind to|we will|i agreed to)\b",
    re.IGNORECASE,
)

ACKNOWLEDGMENT_PATTERNS = re.compile(
    r"^\s*(?:ok|okay|sure|thanks|thank you|got it|understood|right|thik hai|samajh gaya|alright|yes|yeah|yep)\s*$",
    re.IGNORECASE,
)

CORRECTION_PATTERNS = re.compile(
    r"\b(?:actually|correction|instead of|change of plans?|i am not going to|i decided not to|cancel that|rather than|not \d+.*but)\b",
    re.IGNORECASE,
)

PAUSE_PATTERNS = re.compile(
    r"\b(?:leave that aside|pause on that|ignore that for now|wait a second|wait someone is)\b",
    re.IGNORECASE,
)

RESUME_PATTERNS = re.compile(
    r"\b(?:back to|returning to|as i was saying about|anyway back to)\b",
    re.IGNORECASE,
)

RESOLVE_PATTERNS = re.compile(
    r"\b(?:problem is (?:resolved|solved|fixed)|settled peacefully|that is sorted)\b",
    re.IGNORECASE,
)


class TopicTracker:
    """Manages multi-topic tracking, persistent decision ledger, quantitative facts and questions."""

    @classmethod
    def extract_from_messages(cls, messages: list[MessageRecord]) -> TopicContext:
        active_topics: dict[str, TopicRecord] = {}
        paused_topics: dict[str, TopicRecord] = {}
        resolved_topics: dict[str, TopicRecord] = {}
        decision_ledger: list[DecisionRecord] = []
        quantitative_facts: list[QuantitativeFact] = []
        questions: list[QuestionStatus] = []
        resolved_questions: list[str] = []
        unanswered_questions: list[str] = []
        active_topic_id: str | None = None

        turn_num = 0
        last_question_text: str | None = None

        for msg in messages:
            content = msg.content.strip()
            if not content:
                continue

            if msg.role == "assistant":
                # Extract unanswered question at end of message if any
                q_candidates = [line.strip() for line in content.splitlines() if line.strip().endswith("?")]
                if q_candidates:
                    last_q = q_candidates[-1]
                    unanswered_questions = [last_q]
                    last_question_text = last_q
                    questions.append(
                        QuestionStatus(
                            question=last_q,
                            topic_id=active_topic_id or "",
                            source_turn=turn_num,
                            resolved=False,
                        )
                    )
                else:
                    unanswered_questions = []

                # Extract assistant recommendations and attach to current active topic
                if active_topic_id and active_topic_id in active_topics:
                    rec_matches = [
                        s.strip()
                        for s in re.split(r"[.\n]", content)
                        if re.search(r"\b(?:try|consider|follow|prioritize|audit|can you|suggest|recommend)\b", s, re.I)
                        and len(s.strip()) > 15
                    ]
                    for rec in rec_matches[:2]:
                        if rec not in active_topics[active_topic_id].assistant_recommendations:
                            active_topics[active_topic_id].assistant_recommendations.append(rec)
                continue

            # Process User Message
            turn_num += 1

            # If there was a pending question, mark it resolved
            if last_question_text and not ACKNOWLEDGMENT_PATTERNS.match(content):
                resolved_questions.append(last_question_text)
                for q in questions:
                    if q.question == last_question_text and not q.resolved:
                        q.resolved = True
                        q.resolved_turn = turn_num
                        q.answer_summary = truncate(content, 80)
                last_question_text = None
                unanswered_questions = []

            # Check for topic resolution or pause
            if PAUSE_PATTERNS.search(content) and active_topic_id and active_topic_id in active_topics:
                topic = active_topics.pop(active_topic_id)
                topic.status = TopicStatus.PAUSED
                paused_topics[topic.topic_id] = topic
                active_topic_id = None
                continue

            if RESOLVE_PATTERNS.search(content) and active_topic_id and active_topic_id in active_topics:
                topic = active_topics.pop(active_topic_id)
                topic.status = TopicStatus.RESOLVED
                resolved_topics[topic.topic_id] = topic
                active_topic_id = None
                continue

            # Detect topic category
            matched_category: str | None = None
            matched_label: str = "General Concern"
            for cat_id, cat_label, pattern in TOPIC_CUES:
                if pattern.search(content):
                    matched_category = cat_id
                    matched_label = cat_label
                    break

            if matched_category:
                # Find if already existing in active or paused
                existing_topic = None
                for t in active_topics.values():
                    if t.category == matched_category:
                        existing_topic = t
                        break

                if not existing_topic:
                    for t_id, t in list(paused_topics.items()):
                        if t.category == matched_category:
                            existing_topic = paused_topics.pop(t_id)
                            existing_topic.status = TopicStatus.ACTIVE
                            active_topics[t_id] = existing_topic
                            break

                if existing_topic:
                    active_topic_id = existing_topic.topic_id
                    existing_topic.last_active_turn = turn_num
                    clean_fact = truncate(content, 180)
                    if clean_fact not in existing_topic.user_facts:
                        existing_topic.user_facts.append(clean_fact)
                else:
                    new_id = str(uuid.uuid4())[:8]
                    new_topic = TopicRecord(
                        topic_id=new_id,
                        name=matched_label,
                        category=matched_category,
                        status=TopicStatus.ACTIVE,
                        user_facts=[truncate(content, 180)],
                        user_emotions=[],
                        inferred_emotions=[],
                        user_decisions=[],
                        assistant_recommendations=[],
                        turn_introduced=turn_num,
                        last_active_turn=turn_num,
                    )
                    active_topics[new_id] = new_topic
                    active_topic_id = new_id
            else:
                if active_topic_id and active_topic_id in active_topics:
                    active_topics[active_topic_id].last_active_turn = turn_num
                    if not ACKNOWLEDGMENT_PATTERNS.match(content) and len(content) > 10:
                        clean_fact = truncate(content, 120)
                        if clean_fact not in active_topics[active_topic_id].user_facts:
                            active_topics[active_topic_id].user_facts.append(clean_fact)

            target_topic_id = active_topic_id or "general"
            current_topic_record = active_topics.get(active_topic_id) if active_topic_id else None

            # 1. Quantitative Facts Extraction
            q_matches = QUANTITATIVE_PATTERNS.findall(content)
            if q_matches:
                is_corr = bool(CORRECTION_PATTERNS.search(content))
                for q_val in q_matches:
                    clean_val = q_val.strip()
                    if is_corr:
                        for prev_fact in quantitative_facts:
                            if prev_fact.source_turn < turn_num and prev_fact.topic_id == target_topic_id and not prev_fact.is_superseded:
                                prev_fact.is_superseded = True
                    q_fact = QuantitativeFact(
                        id=f"F{len(quantitative_facts)+1}",
                        topic_id=target_topic_id,
                        fact=truncate(content, 160),
                        numerical_value=clean_val,
                        source_turn=turn_num,
                        is_correction=is_corr,
                        created_at=_utcnow(),
                    )
                    quantitative_facts.append(q_fact)
                    if current_topic_record:
                        current_topic_record.quantitative_facts.append(q_fact)

            # 2. Decision Ledger Tracking
            # Invariant: A simple acknowledgment ("ok", "sure") is NEVER a user decision!
            is_ack = bool(ACKNOWLEDGMENT_PATTERNS.match(content))
            has_decision = bool(DECISION_PATTERNS.search(content))
            is_corr = bool(CORRECTION_PATTERNS.search(content))

            if has_decision and not is_ack:
                clean_decision = truncate(content, 140)
                dec_id = f"D{len(decision_ledger)+1}"

                for prev_dec in decision_ledger:
                    if prev_dec.topic_id == target_topic_id and not prev_dec.is_superseded:
                        prev_dec.is_superseded = True
                        prev_dec.superseded_by = dec_id
                        prev_dec.superseded_reason = "Superseded by updated user decision"
                        prev_dec.updated_at = _utcnow()

                d_rec = DecisionRecord(
                    id=dec_id,
                    topic_id=target_topic_id,
                    user_decision=clean_decision,
                    source_message_id=getattr(msg, "id", "") or "",
                    source_turn=turn_num,
                    confirmed=True,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                decision_ledger.append(d_rec)
                if current_topic_record:
                    current_topic_record.decisions.append(d_rec)
                    current_topic_record.user_decisions.append(clean_decision)
            elif is_corr and not is_ack:
                for prev_dec in decision_ledger:
                    if prev_dec.topic_id == target_topic_id and not prev_dec.is_superseded:
                        prev_dec.is_superseded = True
                        prev_dec.superseded_reason = f"Cancelled/corrected at turn {turn_num}: {truncate(content, 80)}"
                        prev_dec.updated_at = _utcnow()

        is_multi = len(active_topics) >= 2

        return TopicContext(
            active_topics=list(active_topics.values()),
            paused_topics=list(paused_topics.values()),
            resolved_topics=list(resolved_topics.values()),
            decision_ledger=decision_ledger,
            quantitative_facts=quantitative_facts,
            questions=questions,
            unanswered_questions=unanswered_questions,
            resolved_questions=resolved_questions,
            active_topic_id=active_topic_id,
            is_multi_topic=is_multi,
        )


@dataclass
class MemoryContext:
    """What the generator is allowed to know about earlier turns."""

    session_id: str
    summary: str = ""
    recent_turns: list[tuple[str, str]] = field(default_factory=list)
    turn_count: int = 0
    cited_refs: list[str] = field(default_factory=list)
    topic_context: TopicContext = field(default_factory=TopicContext)

    def render(self, max_chars: int = 3200) -> str:
        """Prompt-ready block; combines structured topics + decision ledger + history + summary."""
        blocks: list[str] = []

        # 1. Render structured Topic Context if topics are tracked
        if self.topic_context and self.topic_context.active_topics:
            topic_lines = ["ACTIVE CONVERSATION TOPICS (maintain strict separation):"]
            for idx, t in enumerate(self.topic_context.active_topics, 1):
                facts_str = "; ".join(t.user_facts[-2:]) if t.user_facts else "none"
                decisions_str = "; ".join(t.user_decisions[-2:]) if t.user_decisions else "none recorded"
                rec_str = "; ".join(t.assistant_recommendations[-2:]) if t.assistant_recommendations else "none"
                topic_lines.append(f"  Topic {idx} [{t.name}]:")
                topic_lines.append(f"    - User-Stated Facts: {facts_str}")
                topic_lines.append(f"    - User Decisions (Commitments): {decisions_str}")
                topic_lines.append(f"    - Assistant Suggestions (NOT user commitments): {rec_str}")

            if self.topic_context.is_multi_topic:
                topic_lines.append(
                    "  NOTE: Multiple distinct topics are active. If the user's latest message is ambiguous "
                    "('I am stressed', 'what should I do'), clarify which topic they want to focus on "
                    "or acknowledge both instead of assuming."
                )
            blocks.append("\n".join(topic_lines))

        # 2. Render Dedicated Decision Ledger (Persistent across 10, 20, 30, 50 turns)
        if self.topic_context and self.topic_context.decision_ledger:
            d_lines = ["CONFIRMED USER DECISION LEDGER (INDEPENDENT OF ROLLING SUMMARY):"]
            for dec in self.topic_context.decision_ledger[-6:]:
                status_str = "SUPERSEDED" if dec.is_superseded else "CONFIRMED (ACTIVE)"
                extra = f" [{dec.superseded_reason}]" if dec.is_superseded and dec.superseded_reason else ""
                d_lines.append(
                    f"  - [{dec.id}] User Decision: \"{dec.user_decision}\" | Status: {status_str}{extra} (Turn {dec.source_turn})"
                )
            blocks.append("\n".join(d_lines))

        # 3. Render Quantitative Facts & Numerical Amounts
        if self.topic_context and self.topic_context.quantitative_facts:
            q_lines = ["RECORDED QUANTITATIVE FACTS & AMOUNTS (DO NOT ALTER OR FORGET):"]
            active_q = [f for f in self.topic_context.quantitative_facts if not f.is_superseded]
            for qf in active_q[-6:]:
                q_lines.append(f"  - [{qf.numerical_value}]: \"{qf.fact}\" (Turn {qf.source_turn})")
            blocks.append("\n".join(q_lines))

        # 4. Render Question Tracker (Resolved vs Open Questions)
        if self.topic_context and (self.topic_context.resolved_questions or self.topic_context.unanswered_questions):
            ques_lines = ["QUESTION TRACKER:"]
            if self.topic_context.resolved_questions:
                ques_lines.append(f"  - Resolved Questions: {'; '.join(self.topic_context.resolved_questions[-2:])}")
            if self.topic_context.unanswered_questions:
                ques_lines.append(f"  - Open Question Awaiting User: {self.topic_context.unanswered_questions[-1]}")
            blocks.append("\n".join(ques_lines))

        # 5. Render Rolling Summary
        if self.summary:
            blocks.append(f"CONVERSATION SUMMARY SO FAR:\n{self.summary}")

        # 6. Render Recent Turns
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
        return (
            not self.summary
            and not self.recent_turns
            and not (self.topic_context and self.topic_context.active_topics)
        )

    def trace(self) -> MemoryTrace:
        """The observable record of what this context contributes to the prompt."""
        return MemoryTrace(
            turn_count=self.turn_count,
            summary_included=bool(self.summary),
            recent_turns_injected=len(self.recent_turns),
            loaded=True,
        )


class ConversationMemory:
    """Reads history from the repository, tracks topics, and maintains the rolling summary."""

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
        """Assemble the topic-aware memory context for a session."""
        session = await self.repository.get_session(session_id)
        summary = session.rolling_summary if session else ""
        turn_count = session.turn_count if session else 0

        # Load history for recent turns and full session topic extraction
        messages = await self.repository.list_messages(
            session_id, limit=max(self.settings.memory_last_n_turns, 100)
        )

        recent = [
            (record.role, truncate(strip_verse_payload(record.content), 400))
            for record in messages[-self.settings.memory_last_n_turns :]
            if record.role in {"user", "assistant"}
        ]

        cited: list[str] = []
        for record in messages:
            for verse_id in record.citations or ():
                if verse_id not in cited:
                    cited.append(verse_id)

        # Extract structured Topic Context from the message records
        topic_context = TopicTracker.extract_from_messages(messages)

        return MemoryContext(
            session_id=session_id,
            summary=summary,
            recent_turns=recent,
            turn_count=turn_count,
            cited_refs=cited,
            topic_context=topic_context,
        )

    # ----------------------------------------------------------------- write
    async def maybe_update_summary(self, session_id: str) -> str:
        """Refresh the rolling summary when enough turns have accumulated."""
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
        """LLM-backed structured summary with strict commitment separation."""
        if self.llm is None:
            return self._fallback_summary(history, previous)

        transcript = "\n".join(
            f"{record.role}: {truncate(record.content, 300)}" for record in history
        )
        prompt = (
            f"TASK: {TASK_SUMMARY}\n"
            "Summarise this guidance conversation for future context. Maintain strict boundaries:\n"
            "- List the distinct problems/topics discussed.\n"
            "- List only facts explicitly stated by the user.\n"
            "- CRITICAL RULE ON COMMITMENTS: Only record a commitment if the user explicitly stated "
            "intent in first person ('I decided to', 'I will'). An assistant recommendation is NEVER "
            "a user commitment, and user saying 'ok' or 'sure' is NOT a commitment.\n"
            "- Keep within 150 words. Write plainly, third person, no advice.\n\n"
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
