"""Test suite for GitaGuide 2.1 Phase 2: Persistent Decision Ledger & Memory Integrity.

Tests requirements:
1. Independent Decision Ledger persistent across 10, 20, 30, and 50 turns.
2. Invariant: Assistant recommendations are NEVER converted into user decisions without explicit user affirmation.
3. Invariant: Simple acknowledgments ('ok', 'sure', 'thanks') are NOT user decisions.
4. Quantitative facts ('loan of 1 cr', '45 lakhs') preserved verbatim across compaction.
5. Explicit user corrections supersede earlier decisions/facts and mark is_superseded=True.
6. Question tracker distinguishes resolved vs open questions awaiting user response.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.repository import InMemoryRepository, MessageRecord
from app.pipeline.memory import ConversationMemory, TopicTracker


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def memory(repo: InMemoryRepository) -> ConversationMemory:
    settings = Settings(mock_mode=True, memory_last_n_turns=6)
    return ConversationMemory(repository=repo, settings=settings)


def _msg(session_id: str, role: str, content: str, msg_id: str = "") -> MessageRecord:
    return MessageRecord(
        id=msg_id or "",
        session_id=session_id,
        role=role,
        content=content,
    )


# ----------------------------------------------------------------------
# 1. Critical Invariant Tests
# ----------------------------------------------------------------------


def test_assistant_recommendation_is_never_converted_to_user_decision() -> None:
    """Assistant recommending an action and user saying 'ok' must NOT record a confirmed user decision."""
    messages = [
        _msg("s1", "user", "My father took a loan of 1 cr and government is not paying."),
        _msg("s1", "assistant", "Try to consult a legal counsel specializing in government procurement contracts."),
        _msg("s1", "user", "ok"),
    ]
    ctx = TopicTracker.extract_from_messages(messages)
    assert len(ctx.active_topics) >= 1
    topic = ctx.active_topics[0]
    # Invariant: user_decisions must be empty!
    assert topic.user_decisions == []
    assert ctx.decision_ledger == []
    # Recommendation is tracked as assistant recommendation, NOT user decision
    assert len(topic.assistant_recommendations) >= 1
    assert "consult a legal counsel" in topic.assistant_recommendations[0]


def test_explicit_user_affirmation_records_confirmed_decision() -> None:
    """Only explicit user affirmation ('I have decided to...') creates a confirmed DecisionRecord."""
    messages = [
        _msg("s1", "user", "My father took a loan of 1 cr and government is not paying."),
        _msg("s1", "assistant", "Consider sending a legal notice before filing a writ petition."),
        _msg("s1", "user", "I have decided to send a legal notice to the department tomorrow."),
    ]
    ctx = TopicTracker.extract_from_messages(messages)
    assert len(ctx.decision_ledger) == 1
    dec = ctx.decision_ledger[0]
    assert dec.confirmed is True
    assert dec.is_superseded is False
    assert "decided to send a legal notice" in dec.user_decision


# ----------------------------------------------------------------------
# 2. Correction and Superseding Tests
# ----------------------------------------------------------------------


def test_user_correction_supersedes_earlier_decision() -> None:
    """When user explicitly changes plan, previous decision is marked is_superseded=True."""
    messages = [
        _msg("s1", "user", "I will accept the settlement offer from the contractor.", "m1"),
        _msg("s1", "assistant", "Settling could provide immediate liquidity."),
        _msg("s1", "user", "Actually, change of plans: I decided to reject the settlement offer and go to court.", "m3"),
    ]
    ctx = TopicTracker.extract_from_messages(messages)
    assert len(ctx.decision_ledger) == 2
    old_dec = ctx.decision_ledger[0]
    new_dec = ctx.decision_ledger[1]

    assert old_dec.is_superseded is True
    assert old_dec.superseded_by == new_dec.id
    assert new_dec.is_superseded is False
    assert "reject the settlement offer" in new_dec.user_decision


def test_quantitative_facts_preserved_and_corrections_tracked() -> None:
    """Quantitative amounts like 'loan of 1 cr' are preserved, and corrections update facts."""
    messages = [
        _msg("s1", "user", "my father took loan of 1 cr and completed the order"),
        _msg("s1", "assistant", "1 crore is a significant financial obligation. Have you reviewed the contract?"),
        _msg("s1", "user", "Correction: actually the loan amount was 80 lakhs, not 1 cr"),
    ]
    ctx = TopicTracker.extract_from_messages(messages)
    assert len(ctx.quantitative_facts) >= 2
    # Older fact with 1 cr should be superseded by the correction
    fact_1cr = next(f for f in ctx.quantitative_facts if "1 cr" in f.numerical_value or "1 cr" in f.fact)
    fact_80l = next(f for f in ctx.quantitative_facts if "80 lakh" in f.numerical_value or "80 lakh" in f.fact)
    assert fact_1cr.is_superseded is True
    assert fact_80l.is_correction is True
    assert fact_80l.is_superseded is False


# ----------------------------------------------------------------------
# 3. Question Tracker Tests
# ----------------------------------------------------------------------


def test_question_tracker_resolves_questions_when_user_answers() -> None:
    """Assistant questions ending in '?' are tracked and marked resolved when answered."""
    messages = [
        _msg("s1", "user", "I am having serious conflicts with my co-founder."),
        _msg("s1", "assistant", "Did you sign a founders agreement with equity vesting?"),
        _msg("s1", "user", "Yes, we signed a 4-year vesting schedule with a 1-year cliff."),
    ]
    ctx = TopicTracker.extract_from_messages(messages)
    assert len(ctx.questions) == 1
    q = ctx.questions[0]
    assert q.resolved is True
    assert "vesting" in q.question
    assert "founders agreement" in ctx.resolved_questions[0]
    assert ctx.unanswered_questions == []


# ----------------------------------------------------------------------
# 4. Stress Tests at 10, 20, 30, and 50 Turns
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_preserves_decisions_at_10_turns(repo: InMemoryRepository, memory: ConversationMemory) -> None:
    """10-turn multi-topic conversation preserves decisions and numbers in rendered prompt."""
    sid = "sess_10_turns"
    # Turn 1-2: Financial
    await repo.add_message(_msg(sid, "user", "My father took a loan of 1 cr and government is withholding payment."))
    await repo.add_message(_msg(sid, "assistant", "That is a severe financial crisis."))
    # Turn 3-4: Confirmed decision
    await repo.add_message(_msg(sid, "user", "I decided to file a formal RTI and legal notice tomorrow."))
    await repo.add_message(_msg(sid, "assistant", "Documenting the RTI is an important procedural step."))
    # Turn 5-8: Relationship problem introduced
    await repo.add_message(_msg(sid, "user", "On top of that, she broke up with me yesterday."))
    await repo.add_message(_msg(sid, "assistant", "Carrying heartbreak alongside a family crisis is overwhelming."))
    await repo.add_message(_msg(sid, "user", "she cheated on me after 4 years of relationship."))
    await repo.add_message(_msg(sid, "assistant", "Betrayal in a long relationship is devastating."))
    # Turn 9-10: User feelings
    await repo.add_message(_msg(sid, "user", "i m stressed and can't sleep."))
    await repo.add_message(_msg(sid, "assistant", "Take one breath. Which issue feels most urgent right now?"))

    context = await memory.load(sid)
    prompt_text = context.render()

    # Verify decision is preserved in the prompt
    assert "CONFIRMED USER DECISION LEDGER" in prompt_text
    assert "file a formal RTI" in prompt_text
    # Verify quantitative amount is preserved
    assert "1 cr" in prompt_text
    # Verify both topics are tracked separately
    assert len(context.topic_context.active_topics) == 2


@pytest.mark.asyncio
async def test_memory_preserves_decisions_at_20_turns(repo: InMemoryRepository, memory: ConversationMemory) -> None:
    """20-turn conversation beyond MEMORY_WINDOW_TURNS preserves decisions despite rolling summary."""
    sid = "sess_20_turns"
    # Early turns: confirmed decision at Turn 3
    await repo.add_message(_msg(sid, "user", "Starting my company, raised 50 lakhs from angels."))
    await repo.add_message(_msg(sid, "assistant", "Congratulations on the milestone."))
    await repo.add_message(_msg(sid, "user", "I decided to hire a lead engineer before a sales lead."))
    await repo.add_message(_msg(sid, "assistant", "Prioritizing engineering makes sense for technical products."))

    # Generate turns 5 through 20 with general discussions
    for i in range(5, 21):
        role = "user" if i % 2 != 0 else "assistant"
        content = f"Discussing engineering architecture iteration {i} and market feedback."
        await repo.add_message(_msg(sid, role, content))

    context = await memory.load(sid)
    prompt_text = context.render()

    # Confirmed decision from Turn 3 MUST be present in Decision Ledger
    assert "CONFIRMED USER DECISION LEDGER" in prompt_text
    assert "hire a lead engineer" in prompt_text
    # Numerical amount preserved
    assert "50 lakhs" in prompt_text


@pytest.mark.asyncio
async def test_memory_preserves_decisions_at_30_turns(repo: InMemoryRepository, memory: ConversationMemory) -> None:
    """30-turn conversation with topic switching and superseded decision preserves state."""
    sid = "sess_30_turns"
    # Turn 1: Decision A
    await repo.add_message(_msg(sid, "user", "I will accept the job offer in Singapore with 120k salary."))
    await repo.add_message(_msg(sid, "assistant", "Relocating for a career move is a big step."))

    # Turn 5: User supersedes Decision A with Decision B
    await repo.add_message(_msg(sid, "user", "Actually, change of plan: I decided to decline Singapore and stay in Bangalore."))
    await repo.add_message(_msg(sid, "assistant", "Staying local maintains family proximity."))

    # Fill up to 30 turns
    for i in range(6, 31):
        role = "user" if i % 2 != 0 else "assistant"
        content = f"Workplace project updates at turn {i}."
        await repo.add_message(_msg(sid, role, content))

    context = await memory.load(sid)
    prompt_text = context.render()

    assert "CONFIRMED USER DECISION LEDGER" in prompt_text
    assert "decline Singapore and stay in Bangalore" in prompt_text
    # Decision A should be marked SUPERSEDED
    assert "SUPERSEDED" in prompt_text


@pytest.mark.asyncio
async def test_memory_preserves_decisions_at_50_turns(repo: InMemoryRepository, memory: ConversationMemory) -> None:
    """50-turn marathon multi-topic conversation retains decision ledger and quantitative facts."""
    sid = "sess_50_turns"
    # Turn 1: Loan facts
    await repo.add_message(_msg(sid, "user", "My father took a loan of 1 cr and government completed order not paid."))
    await repo.add_message(_msg(sid, "assistant", "A 1 crore loan default risk requires urgent procedural steps."))
    # Turn 3: Confirmed decision
    await repo.add_message(_msg(sid, "user", "I decided to file a writ petition in the High Court."))
    await repo.add_message(_msg(sid, "assistant", "A writ petition under Article 226 is an established remedy."))

    # Turns 5 to 50: Continuous conversation across 46 turns
    for i in range(5, 51):
        role = "user" if i % 2 != 0 else "assistant"
        content = f"Turn {i} conversation update on emotional resilience and day to day focus."
        await repo.add_message(_msg(sid, role, content))

    context = await memory.load(sid)
    prompt_text = context.render()

    # The Decision Ledger MUST retain the writ petition decision made at Turn 3
    assert "CONFIRMED USER DECISION LEDGER" in prompt_text
    assert "file a writ petition" in prompt_text
    # Quantitative fact '1 cr' MUST be retained
    assert "1 cr" in prompt_text

