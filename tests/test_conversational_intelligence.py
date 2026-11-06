"""Regression tests for conversational intelligence and contextual disambiguation.

Verifies:
1. "console" in personal/emotional distress is understood as consolation, NEVER out-of-scope or coding console.
2. Emotional distress, frustration, and calming requests map to emotional_support and guidance/followup.
3. Multi-turn conversation preserves context and avoids repetitive or irrelevant responses.
4. Distinguishes consolation, practical advice, explanation, and out-of-scope topics.
"""

from __future__ import annotations

import pytest

from app.db.repository import InMemoryRepository
from app.llm.fake import FakeLLMProvider
from app.pipeline.intent import IntentClassifier, heuristic_intent_scores
from app.pipeline.orchestrator import GuidanceOrchestrator
from app.pipeline.router import ConversationRouter, Route
from app.pipeline.schemas import IntentLabel, UserTurn


@pytest.fixture
def routed(orchestrator: GuidanceOrchestrator) -> GuidanceOrchestrator:
    orchestrator.router = ConversationRouter(llm=FakeLLMProvider())
    return orchestrator


# 1. Ambiguity & homonym resolution: "console" == consolation
@pytest.mark.parametrize(
    "phrase",
    [
        "i need console",
        "console for my conflict",
        "please console me",
        "can you console me",
        "i need solace and comfort",
        "how to calm my mind?",
        "i am frustrated and angry",
    ],
)
def test_console_and_calming_phrases_prioritize_emotional_support(phrase: str) -> None:
    """Keyword prior must score emotional_support highest, never other or out-of-scope."""
    scores = heuristic_intent_scores(phrase)
    assert max(scores, key=scores.get) == IntentLabel.EMOTIONAL_SUPPORT.value


@pytest.mark.parametrize(
    "phrase",
    [
        "i need console",
        "console for my conflict",
        "can you console me",
        "i want to calm my mind",
    ],
)
async def test_console_never_routes_to_out_of_scope(phrase: str) -> None:
    """Ambiguous emotional phrases must never route to OUT_OF_SCOPE."""
    router = ConversationRouter(llm=FakeLLMProvider())
    decision = await router.route(phrase, memory=None)
    assert decision.route in (Route.GUIDANCE, Route.FOLLOWUP)
    assert decision.route is not Route.OUT_OF_SCOPE


# 2. Genuine out-of-scope vs emotional ambiguity
@pytest.mark.parametrize(
    "phrase",
    [
        "write me a python script to parse csv",
        "give me a chocolate cake recipe",
        "what is the latest cricket score",
        "help me write javascript code for my console",
    ],
)
async def test_genuine_technical_or_trivia_routes_to_out_of_scope(phrase: str) -> None:
    router = ConversationRouter(llm=FakeLLMProvider())
    decision = await router.route(phrase, memory=None)
    assert decision.route is Route.OUT_OF_SCOPE


# 3. End-to-end multi-turn conversation flow
async def test_interview_frustration_and_console_multi_turn_flow(
    routed: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """The reported bug: user describes interview frustration, then says 'i need console'.

    The system must NOT respond with 'You need a console... I can't generate code'.
    It must provide empathetic, scripture-grounded consolation.
    """
    session_id = "test-interview-console-session"

    # Turn 1: User describes interview cancellation and frustration
    turn_1 = await routed.handle_turn(
        UserTurn(
            message=(
                "today i had an interview of jio star company, they called us at 8:30 am "
                "and we were waiting for our chance until 5:30 pm when they cancelled with "
                "no explanation. this made me so frustrated and angry at the placement coordinator"
            ),
            session_id=session_id,
        )
    )
    assert turn_1.crisis is False
    assert turn_1.route.route == Route.GUIDANCE.value
    assert turn_1.retrieval is not None and len(turn_1.retrieval.results) > 0
    assert turn_1.answer
    assert "not a search engine" not in turn_1.answer.lower()
    assert "coding assistant" not in turn_1.answer.lower()

    # Turn 2: User says "i need console"
    turn_2 = await routed.handle_turn(
        UserTurn(
            message="i need console",
            session_id=session_id,
        )
    )
    assert turn_2.crisis is False
    assert turn_2.route.route in (Route.FOLLOWUP.value, Route.GUIDANCE.value)
    assert turn_2.route.route != Route.OUT_OF_SCOPE.value
    assert turn_2.answer
    # Must NOT contain the out-of-scope technical rejection
    assert "coding assistant" not in turn_2.answer.lower()
    assert "cannot generate code" not in turn_2.answer.lower()
    assert "tech specs" not in turn_2.answer.lower()

    # Turn 3: User asks "how to calm my mind?"
    turn_3 = await routed.handle_turn(
        UserTurn(
            message="how to calm my mind?",
            session_id=session_id,
        )
    )
    assert turn_3.crisis is False
    assert turn_3.route.route in (Route.FOLLOWUP.value, Route.GUIDANCE.value)
    assert turn_3.retrieval is not None
    assert turn_3.answer


# 4. Intent categorization precision
@pytest.mark.parametrize(
    ("text", "expected_intent"),
    [
        ("I feel completely broken and alone after this breakup", IntentLabel.EMOTIONAL_SUPPORT),
        ("My manager took all credit for my project and I want to resign", IntentLabel.PROFESSIONAL_DILEMMA),
        ("What does Krishna say to Arjuna about duty in chapter 2?", IntentLabel.SCRIPTURE_QUESTION),
        ("How should I decide whether to get married or pursue higher studies?", IntentLabel.SEEK_GUIDANCE),
    ],
)
async def test_intent_classification_precision(
    text: str, expected_intent: IntentLabel
) -> None:
    classifier = IntentClassifier(llm=FakeLLMProvider(), use_llm=True)
    res = await classifier.classify(text)
    assert res.intent == expected_intent

