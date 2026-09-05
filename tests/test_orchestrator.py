"""Unit tests for the orchestrator: stage order, short-circuit, memory, logging."""

from __future__ import annotations

import pytest

from app.db.repository import InMemoryRepository
from app.llm.fake import FakeLLMProvider
from app.pipeline.memory import ConversationMemory
from app.pipeline.orchestrator import (
    STAGE_GENERATION,
    STAGE_RETRIEVAL,
    STAGE_SAFETY_INPUT,
    STAGE_SAFETY_OUTPUT,
    GuidanceOrchestrator,
    build_orchestrator,
)
from app.pipeline.schemas import (
    EmotionResult,
    IntentLabel,
    IntentResult,
    SemanticStatus,
    UserTurn,
)

WORK_QUERY = "My manager took credit for my project and I am furious. What should I do?"
CRISIS_QUERY = "I want to end my life tonight"


# ------------------------------------------------------------------ happy path
async def test_full_pipeline_runs_every_stage(orchestrator: GuidanceOrchestrator) -> None:
    result = await orchestrator.handle_turn(UserTurn(message=WORK_QUERY))
    assert result.stages_run == [
        "emotion",
        "intent",
        STAGE_SAFETY_INPUT,
        "memory",
        STAGE_RETRIEVAL,
        STAGE_GENERATION,
        STAGE_SAFETY_OUTPUT,
        "persist",
    ]
    assert not result.crisis
    assert result.answer
    assert result.retrieval is not None and result.retrieval.results
    assert result.generation is not None and result.generation.grounded
    assert result.citations


async def test_safety_runs_before_retrieval_and_generation(
    orchestrator: GuidanceOrchestrator,
) -> None:
    """Rule 4, asserted on the recorded stage order."""
    result = await orchestrator.handle_turn(UserTurn(message=WORK_QUERY))
    order = result.stages_run
    assert order.index(STAGE_SAFETY_INPUT) < order.index(STAGE_RETRIEVAL)
    assert order.index(STAGE_SAFETY_INPUT) < order.index(STAGE_GENERATION)
    assert order.index(STAGE_GENERATION) < order.index(STAGE_SAFETY_OUTPUT)


async def test_trace_is_persisted_and_explainable(
    orchestrator: GuidanceOrchestrator,
) -> None:
    result = await orchestrator.handle_turn(UserTurn(message=WORK_QUERY))
    record = await orchestrator.explain(result.message_id)
    assert record is not None
    assert record.role == "assistant"
    assert record.retrieval and record.retrieval["results"]
    assert record.citations == result.citations


# --------------------------------------------------------------- short circuit
async def test_crisis_short_circuits_before_retrieval(
    orchestrator: GuidanceOrchestrator,
) -> None:
    result = await orchestrator.handle_turn(UserTurn(message=CRISIS_QUERY))
    assert result.crisis
    assert result.retrieval is None
    assert result.generation is None
    assert STAGE_RETRIEVAL not in result.stages_run
    assert STAGE_GENERATION not in result.stages_run
    assert "14416" in result.answer


async def test_crisis_never_calls_the_llm(
    orchestrator: GuidanceOrchestrator, llm: FakeLLMProvider
) -> None:
    await orchestrator.handle_turn(UserTurn(message=CRISIS_QUERY))
    # The intent classifier is the only permitted call on a crisis turn.
    assert all("intent_classification" in call for call in llm.calls)


async def test_crisis_turn_is_flagged_in_storage(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    result = await orchestrator.handle_turn(UserTurn(message=CRISIS_QUERY))
    record = await repository.get_message(result.message_id)
    assert record is not None and record.crisis


async def test_safety_triggers_are_written_to_the_log(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    await orchestrator.handle_turn(UserTurn(message=CRISIS_QUERY))
    logs = await repository.list_safety_logs()
    assert logs, "the gate must persist an audit row"
    assert logs[0].blocked
    assert logs[0].stage == "input"
    assert logs[0].categories


async def test_clean_turn_writes_no_safety_log(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    await orchestrator.handle_turn(UserTurn(message="What does BG 2.47 mean?"))
    assert await repository.list_safety_logs() == []


async def test_semantic_near_miss_is_persisted_without_blocking(
    orchestrator: GuidanceOrchestrator,
    repository: InMemoryRepository,
) -> None:
    class NearMissClassifier:
        async def classify(
            self, text: str, emotion: EmotionResult | None = None
        ) -> IntentResult:
            return IntentResult(
                intent=IntentLabel.EMOTIONAL_SUPPORT,
                confidence=0.65,
                scores={
                    IntentLabel.EMOTIONAL_SUPPORT.value: 0.65,
                    IntentLabel.CRISIS.value: 0.35,
                },
                semantic_status=SemanticStatus.OK,
                semantic_provider="test",
                semantic_model="risk-fixture",
                semantic_crisis_score=0.35,
            )

    orchestrator.intent_classifier = NearMissClassifier()  # type: ignore[assignment]
    result = await orchestrator.handle_turn(UserTurn(message="I feel very low today"))
    logs = await repository.list_safety_logs()
    assert not result.crisis
    assert len(logs) == 1
    assert not logs[0].blocked
    assert logs[0].stage == "input"
    assert logs[0].triggers["items"] == []
    assert logs[0].triggers["signals"][0]["score"] == 0.35


@pytest.mark.parametrize("status", [SemanticStatus.UNAVAILABLE, SemanticStatus.MALFORMED])
async def test_unusable_semantic_result_is_persisted_for_audit(
    orchestrator: GuidanceOrchestrator,
    repository: InMemoryRepository,
    status: SemanticStatus,
) -> None:
    class UnusableClassifier:
        async def classify(
            self, text: str, emotion: EmotionResult | None = None
        ) -> IntentResult:
            return IntentResult(
                intent=IntentLabel.OTHER,
                confidence=1.0,
                scores={IntentLabel.OTHER.value: 1.0},
                semantic_status=status,
                semantic_provider="test",
                semantic_model="risk-fixture",
            )

    orchestrator.intent_classifier = UnusableClassifier()  # type: ignore[assignment]
    result = await orchestrator.handle_turn(UserTurn(message="Should I take the new job?"))
    logs = await repository.list_safety_logs()
    assert not result.crisis
    assert len(logs) == 1
    assert not logs[0].blocked
    assert logs[0].categories == ["none"]
    assert status.value in logs[0].triggers["signals"][0]["detail"]


# --------------------------------------------------------------------- session
async def test_session_is_created_and_reused(orchestrator: GuidanceOrchestrator) -> None:
    first = await orchestrator.handle_turn(UserTurn(message="I feel stuck in my career"))
    second = await orchestrator.handle_turn(
        UserTurn(message="and my family keeps pushing me", session_id=first.session_id)
    )
    assert second.session_id == first.session_id


async def test_history_reaches_the_prompt(
    orchestrator: GuidanceOrchestrator, llm: FakeLLMProvider
) -> None:
    first = await orchestrator.handle_turn(UserTurn(message="Should I resign from my job?"))
    llm.calls.clear()
    await orchestrator.handle_turn(
        UserTurn(message="What if my parents object?", session_id=first.session_id)
    )
    answer_prompts = [call for call in llm.calls if "grounded_answer" in call]
    assert answer_prompts
    assert "RECENT TURNS" in answer_prompts[0]
    assert "resign" in answer_prompts[0]


async def test_rolling_summary_is_created_after_enough_turns(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    result = await orchestrator.handle_turn(UserTurn(message="I am unhappy at work"))
    session_id = result.session_id
    for index in range(5):
        await orchestrator.handle_turn(
            UserTurn(message=f"another thought number {index} about my career", session_id=session_id)
        )
    session = await repository.get_session(session_id)
    assert session is not None
    assert session.turn_count >= orchestrator.settings.memory_summary_trigger_turns
    assert session.rolling_summary, "the summary should have been refreshed"


async def test_memory_context_is_empty_for_a_new_session(
    repository: InMemoryRepository, llm: FakeLLMProvider
) -> None:
    memory = ConversationMemory(repository=repository, llm=llm)
    session = await repository.create_session()
    context = await memory.load(session.id)
    assert context.is_empty
    assert context.render() == ""


# ------------------------------------------------------- memory observability
async def test_memory_trace_is_reported_and_grows(
    orchestrator: GuidanceOrchestrator,
) -> None:
    """The memory stage reports what it injected, turn by turn.

    This exists because an empty ``memory_summary`` at turn 2 was
    indistinguishable from a broken memory channel. ``turn_count`` counts
    messages and the user's own message is persisted before the stage runs, so
    exchange 1 reports 1 and exchange 2 reports 3.
    """
    first = await orchestrator.handle_turn(UserTurn(message="I feel stuck in my career"))
    assert first.memory.loaded is True
    assert first.memory.turn_count == 1
    assert first.memory.recent_turns_injected == 1
    assert first.memory.summary_included is False

    second = await orchestrator.handle_turn(
        UserTurn(message="and my family keeps pushing me", session_id=first.session_id)
    )
    assert second.memory.turn_count == 3
    assert second.memory.recent_turns_injected == 3
    assert second.memory.summary_included is False


async def test_memory_trace_reports_the_summary_once_there_is_one(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """``summary_included`` must flip only when a summary actually exists."""
    result = await orchestrator.handle_turn(UserTurn(message="I am unhappy at work"))
    session_id = result.session_id
    for index in range(5):
        result = await orchestrator.handle_turn(
            UserTurn(
                message=f"another thought number {index} about my career",
                session_id=session_id,
            )
        )

    session = await repository.get_session(session_id)
    assert session is not None and session.rolling_summary

    after = await orchestrator.handle_turn(
        UserTurn(message="so what should I do next?", session_id=session_id)
    )
    assert after.memory.summary_included is True
    # The last-N window caps injection; it must not grow without bound.
    assert (
        after.memory.recent_turns_injected
        <= orchestrator.settings.memory_last_n_turns
    )


async def test_memory_trace_says_the_stage_never_ran_on_a_crisis_turn(
    orchestrator: GuidanceOrchestrator,
) -> None:
    """The gate short-circuits before memory, and the trace says so plainly."""
    result = await orchestrator.handle_turn(UserTurn(message="I want to kill myself"))
    assert result.crisis is True
    assert result.memory.loaded is False
    assert result.memory.recent_turns_injected == 0
    assert result.memory.summary_included is False
    # turn_count is a fact about the session, so it is still reported.
    assert result.memory.turn_count >= 1


async def test_memory_trace_is_persisted_on_the_message_row(
    orchestrator: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """Persisted, not only returned - ``GET /explain`` serves it back."""
    result = await orchestrator.handle_turn(UserTurn(message="My team ignores me"))
    record = await repository.get_message(result.message_id)
    assert record is not None
    assert record.memory == result.memory.model_dump(mode="json")


# -------------------------------------------------------------- output safety
async def test_unsafe_generation_is_replaced(orchestrator: GuidanceOrchestrator) -> None:
    """If the model emits unsafe text, the user must not see it."""
    # Learn a genuinely retrieved id first, so the unsafe answer survives the
    # citation check and is stopped by the output gate rather than by grounding.
    baseline = await orchestrator.handle_turn(UserTurn(message=WORK_QUERY))
    cited = baseline.citations[0]
    unsafe = f"You deserve this suffering; this is your karma for what you did. {cited}"
    orchestrator.generator.llm = FakeLLMProvider(scripted=[unsafe, unsafe])

    result = await orchestrator.handle_turn(UserTurn(message=WORK_QUERY))
    assert result.output_safety.blocked
    assert "you deserve this suffering" not in result.answer.lower()


# ----------------------------------------------------------------- wiring/ops
def test_default_wiring_is_fully_offline() -> None:
    orchestrator = build_orchestrator()
    assert orchestrator.repository.backend == "in-memory"
    assert orchestrator.retriever.backend == "in-memory-hybrid"
    assert orchestrator.generator.llm.is_fake


async def test_health_reports_backends(orchestrator: GuidanceOrchestrator) -> None:
    health = await orchestrator.health()
    assert health["emotion_backend"] == "lexicon"
    assert health["retrieval_backend"] == "in-memory-hybrid"
    assert health["mock_mode"] is True


async def test_empty_message_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValueError):
        UserTurn(message="")
