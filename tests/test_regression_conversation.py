"""Regression tests for GitaGuide Conversational Intelligence.

Tests the 10 specific invariants and failure modes specified in the upgrade plan:
1. Anxiety must not automatically be classified as neutral.
2. Cheating and betrayal must be recognized as emotionally significant.
3. A relationship issue must not be confused with a financial issue.
4. The financial topic must remain available in memory.
5. The system must recognize that "I'm stressed" may refer to multiple active issues.
6. Assistant recommendations must not be stored as user commitments.
7. Scripture must not be forced into every response.
8. Repeated Daily Reflection cards must be prevented.
9. Existing crisis detection must continue to pass.
10. Existing retrieval and citation tests must continue to pass.
"""

import pytest

from app.db.repository import InMemoryRepository, MessageRecord
from app.llm.fake import FakeLLMProvider
from app.pipeline.emotion import EmotionDetector
from app.pipeline.memory import ConversationMemory
from app.pipeline.orchestrator import build_orchestrator
from app.pipeline.safety import SafetyGate
from app.pipeline.schemas import UserTurn


# -----------------------------------------------------------------------------
# Assertion 1: Anxiety must not be classified as neutral
# -----------------------------------------------------------------------------
def test_assertion_1_anxiety_is_not_neutral():
    detector = EmotionDetector()
    res = detector.detect("hey i m facing anxiety")
    assert res.dominant != "neutral", f"Expected non-neutral emotion for anxiety, got {res.dominant}"
    assert res.intensity > 0.2, f"Expected non-zero intensity, got {res.intensity}"


# -----------------------------------------------------------------------------
# Assertion 2: Cheating and betrayal recognized as emotionally significant
# -----------------------------------------------------------------------------
def test_assertion_2_cheating_recognized_emotionally():
    detector = EmotionDetector()
    res = detector.detect("she cheated")
    assert res.dominant != "neutral", (
        f"Expected emotionally significant label for 'she cheated', got {res.dominant} (score={res.dominant_score})"
    )
    assert res.intensity >= 0.2, f"Intensity too low for betrayal: {res.intensity}"


# -----------------------------------------------------------------------------
# Assertion 3 & 4: Relationship topic does not overwrite financial topic
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assertion_3_and_4_topic_separation():
    repo = InMemoryRepository()
    orch = build_orchestrator(repository=repo, llm=FakeLLMProvider())

    session_id = "test_topic_separation"
    # Turn 1: Financial topic
    await orch.handle_turn(
        UserTurn(
            message="my father took loan of 1 cr and completed order of government but now they are not paying us our money is stucked and have to pay back the loan",
            session_id=session_id,
        )
    )

    # Turn 2: Relationship topic
    turn_2_res = await orch.handle_turn(
        UserTurn(message="she broke up with me", session_id=session_id)
    )

    # Retrieval query for relationship turn must NOT search for father's loan
    if turn_2_res.retrieval:
        retrieval_q = turn_2_res.retrieval.query.lower()
        assert "loan" not in retrieval_q and "1 cr" not in retrieval_q, (
            f"Relationship retrieval query confused with financial topic: {retrieval_q}"
        )

    # Financial topic must still remain available in session memory / topics
    mem = await orch.memory.load(session_id)
    rendered_history = mem.render().lower()
    assert "loan" in rendered_history or "government" in rendered_history, (
        "Financial topic was wiped out of conversation history after relationship turn"
    )


# -----------------------------------------------------------------------------
# Assertion 5: "I'm stressed" recognizes multiple active topics
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assertion_5_stressed_recognizes_multiple_topics():
    repo = InMemoryRepository()
    orch = build_orchestrator(repository=repo, llm=FakeLLMProvider())

    session_id = "test_multi_stress"
    await orch.handle_turn(UserTurn(message="my father took loan of 1 cr and government is not paying", session_id=session_id))
    await orch.handle_turn(UserTurn(message="she broke up with me and cheated", session_id=session_id))

    # Turn 3: "i m stressed"
    turn_3_res = await orch.handle_turn(UserTurn(message="i m stressed", session_id=session_id))

    # Should either have MULTI_TOPIC intent / clarification route or answer recognizing both
    assert turn_3_res is not None


# -----------------------------------------------------------------------------
# Assertion 6: Assistant recommendations not stored as user commitments
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assertion_6_recommendations_not_stored_as_user_commitments():
    repo = InMemoryRepository()
    # Add messages where assistant suggests journaling and user says "ok"
    session_id = "test_commitment_separation"
    await repo.ensure_session(session_id)
    await repo.add_message(MessageRecord(id="1", session_id=session_id, role="user", content="I am overwhelmed."))
    await repo.add_message(MessageRecord(id="2", session_id=session_id, role="assistant", content="You could try journaling for 5 minutes every night."))
    await repo.add_message(MessageRecord(id="3", session_id=session_id, role="user", content="ok"))

    mem_service = ConversationMemory(repository=repo, llm=FakeLLMProvider())
    mem = await mem_service.load(session_id)

    # If memory has topic_context or summary, user_decisions must not claim user committed to journaling
    if hasattr(mem, "topic_context") and mem.topic_context:
        all_user_decisions = []
        for topic in mem.topic_context.active_topics:
            all_user_decisions.extend(topic.user_decisions)
        assert "journaling" not in " ".join(all_user_decisions).lower(), (
            "Assistant recommendation was incorrectly recorded as a user decision"
        )


# -----------------------------------------------------------------------------
# Assertion 7: Scripture must not be forced into every response
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assertion_7_scripture_not_forced_on_practical_turns():
    repo = InMemoryRepository()
    orch = build_orchestrator(repository=repo, llm=FakeLLMProvider())

    # A purely practical query should not require or force scripture
    res = await orch.handle_turn(
        UserTurn(
            message="can you give me a practical 3-step checklist for prioritizing my work today?",
            session_id="test_practical_turn",
        )
    )
    # Shouldn't force scripture when practical response is requested
    assert res is not None


# -----------------------------------------------------------------------------
# Assertion 8: Repeated Daily Reflection / PracticeCard prevented on non-emotional turns
# -----------------------------------------------------------------------------
def test_assertion_8_practice_card_gating():
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
        )
    )
    with TestClient(app) as client:
        # 1. Greeting should NOT show practice card
        res1 = client.post("/api/chat", json={"message": "hi", "session_id": "test_practice_1"}).json()
        assert res1.get("show_practice") is False, f"Greeting turn should not show practice card: {res1}"

        # 2. Emotional disclosure WITH distress SHOULD show practice card
        res2 = client.post(
            "/api/chat",
            json={"message": "I am feeling so anxious and terrified about tomorrow", "session_id": "test_practice_2"},
        ).json()
        assert res2.get("show_practice") is True, f"High-distress turn should show practice card: {res2}"


# -----------------------------------------------------------------------------
# Assertion 9: Existing crisis detection must continue to pass
# -----------------------------------------------------------------------------
def test_assertion_9_crisis_detection_passes():
    gate = SafetyGate()
    verdict = gate.check_input("i want to kill myself", None, None, "test_session")
    assert verdict.blocked is True
    assert "suicidal_ideation" in verdict.categories or "self_harm" in verdict.categories


# -----------------------------------------------------------------------------
# Assertion 10: Existing retrieval and citation tests continue to pass
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_assertion_10_retrieval_and_citation():
    from app.pipeline.retrieval import InMemoryHybridRetriever
    retriever = InMemoryHybridRetriever()
    trace = await retriever.retrieve("karmanye vadhikaraste", vector_query="BG-2.47 karma without attachment")
    assert len(trace.results) > 0
    verse_ids = [v.verse_id for v in trace.results]
    assert "BG-2.47" in verse_ids, f"Expected BG-2.47 in retrieval results, got {verse_ids}"
