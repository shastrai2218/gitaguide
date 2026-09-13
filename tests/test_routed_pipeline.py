"""The router inside the pipeline: what it skips, and what it may never touch.

Two questions, and only one of them is about quality.

The quality one: does a greeting still get a verse? Before Phase 1b every
message ran retrieval, so "hi" came back with a teaching on detachment. These
tests assert that retrieval genuinely does not run - not that the verse was
hidden afterwards, which would be the same cost and the same prompt with a
nicer surface.

The safety one: the gate runs BEFORE the router, so a crisis turn returns from
the orchestrator without the router ever being consulted. That is an ordering
claim, and ordering claims rot silently, so every crisis phrasing the gate
knows about is run through the whole pipeline here with the router wired in.
"""

from __future__ import annotations

import pytest

from app.db.repository import InMemoryRepository, MessageRecord
from app.llm.base import LLMProvider, LLMResponse
from app.llm.fake import FakeLLMProvider
from app.pipeline.orchestrator import (
    STAGE_GENERATION,
    STAGE_RETRIEVAL,
    STAGE_ROUTER,
    STAGE_SAFETY_INPUT,
    GuidanceOrchestrator,
)
from app.pipeline.router import ConversationRouter, Route, RouterDecisionStage
from app.pipeline.schemas import UserTurn
from tests.test_safety import NOT_ALIVE_PHRASES

CRISIS_PHRASES = [*NOT_ALIVE_PHRASES, "I want to kill myself", "I want to end my life tonight"]


@pytest.fixture
def routed(orchestrator: GuidanceOrchestrator) -> GuidanceOrchestrator:
    """The shared orchestrator with a real (offline) router wired in."""
    orchestrator.router = ConversationRouter(llm=FakeLLMProvider())
    return orchestrator


# ----------------------------------------------------------------- the safety half
@pytest.mark.parametrize("message", CRISIS_PHRASES)
async def test_crisis_still_short_circuits_with_the_router_in_front(
    routed: GuidanceOrchestrator, message: str
) -> None:
    """The gate's verdict is untouched, and the router never ran."""
    result = await routed.handle_turn(UserTurn(message=message))
    assert result.crisis is True
    assert result.retrieval is None
    assert result.generation is None
    assert STAGE_ROUTER not in result.stages_run
    assert STAGE_RETRIEVAL not in result.stages_run
    assert STAGE_GENERATION not in result.stages_run


@pytest.mark.parametrize("message", CRISIS_PHRASES)
async def test_a_crisis_turn_is_labelled_crisis_by_the_gate_not_a_model(
    routed: GuidanceOrchestrator, message: str
) -> None:
    """The trace must be able to prove the router was not in the path."""
    result = await routed.handle_turn(UserTurn(message=message))
    assert result.route.route == Route.CRISIS.value
    assert result.route.stage == RouterDecisionStage.GATE.value
    assert result.route.model == ""
    assert result.route.fell_back is False


async def test_the_gate_still_precedes_the_router_in_the_recorded_order(
    routed: GuidanceOrchestrator,
) -> None:
    result = await routed.handle_turn(
        UserTurn(message="My manager took credit for my project and I am furious.")
    )
    order = result.stages_run
    assert order.index(STAGE_SAFETY_INPUT) < order.index(STAGE_ROUTER)
    assert order.index(STAGE_ROUTER) < order.index(STAGE_RETRIEVAL)


async def test_a_router_that_explodes_cannot_block_a_turn(
    routed: GuidanceOrchestrator,
) -> None:
    """The worst case is the pre-Phase-1b behaviour, not a failed request."""

    class Exploding(LLMProvider):
        name = "boom"
        model = "boom"

        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            raise RuntimeError("router is on fire")

        async def aclose(self) -> None:  # pragma: no cover
            return None

    routed.router = ConversationRouter(llm=Exploding())
    result = await routed.handle_turn(UserTurn(message="Should I quit my job for a startup?"))
    assert result.answer
    assert result.retrieval is not None and result.retrieval.results
    assert result.route.route == Route.GUIDANCE.value
    assert result.route.fell_back is True


# -------------------------------------------------------------- the quality half
@pytest.mark.parametrize("message", ["hi", "namaste", "kem cho", "thanks", "ok", "bye"])
async def test_a_greeting_never_reaches_the_retriever(
    routed: GuidanceOrchestrator, message: str
) -> None:
    """Skipped, not hidden. The stage list is the evidence."""
    result = await routed.handle_turn(UserTurn(message=message))
    assert STAGE_RETRIEVAL not in result.stages_run
    assert result.retrieval is None
    assert result.citations == []
    assert result.answer


@pytest.mark.parametrize("message", ["hi", "thanks", "who are you"])
async def test_a_no_retrieval_reply_carries_no_verse_reference(
    routed: GuidanceOrchestrator, message: str
) -> None:
    """A reference the reader cannot open in the citation surface is a dead link."""
    import re

    result = await routed.handle_turn(UserTurn(message=message))
    assert not re.search(r"\b(?:BG|AS)-\d", result.answer), result.answer


async def test_the_output_safety_gate_still_runs_on_a_greeting(
    routed: GuidanceOrchestrator,
) -> None:
    """The branch skips retrieval, not the gate. The gate is outside it."""
    from app.pipeline.orchestrator import STAGE_SAFETY_OUTPUT

    result = await routed.handle_turn(UserTurn(message="hello"))
    assert STAGE_SAFETY_OUTPUT in result.stages_run
    assert result.output_safety is not None


async def test_a_guidance_turn_still_retrieves_and_cites(
    routed: GuidanceOrchestrator,
) -> None:
    """The point of the router is to skip the turns that do not need this one."""
    result = await routed.handle_turn(
        UserTurn(message="My manager takes credit for my work and I am getting bitter.")
    )
    assert STAGE_RETRIEVAL in result.stages_run
    assert result.retrieval is not None and result.retrieval.results
    assert result.citations
    assert result.generation is not None and result.generation.grounded
    assert result.route.route == Route.GUIDANCE.value


async def test_the_route_is_recorded_on_the_generation_result(
    routed: GuidanceOrchestrator,
) -> None:
    """So the tiers can be measured against what each actually produced."""
    greeting = await routed.handle_turn(UserTurn(message="hi"))
    assert greeting.generation is not None
    assert greeting.generation.answer_route == Route.GREETING.value

    guidance = await routed.handle_turn(UserTurn(message="Should I report a safety problem?"))
    assert guidance.generation is not None
    assert guidance.generation.answer_route == Route.GUIDANCE.value


async def test_a_no_retrieval_turn_persists_without_a_retrieval_trace(
    routed: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """The stored row must not claim a retrieval that never happened."""
    result = await routed.handle_turn(UserTurn(message="hi"))
    stored = await repository.get_message(result.message_id)
    assert stored is not None
    assert stored.retrieval in ({}, None)
    assert stored.generation["answer_route"] == Route.GREETING.value


# ------------------------------------------------------------------- follow-ups
async def seed_anger_session(repository: InMemoryRepository, session_id: str) -> None:
    await repository.ensure_session(session_id, "en-IN")
    for role, content in (
        ("user", "boss ne sabke saamne daanta"),
        ("assistant", "Anger comes from where the injury landed (BG-2.62)."),
    ):
        await repository.add_message(
            MessageRecord(
                id=f"{session_id}-{role}",
                session_id=session_id,
                role=role,
                content=content,
                citations=["BG-2.62"] if role == "assistant" else [],
            )
        )


async def test_a_model_routed_followup_retrieves_using_the_rewrite(
    routed: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """A follow-up retrieves on the situation, not on its own contentless words."""
    import json

    class Rewrites(FakeLLMProvider):
        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            if "TASK: conversation_route" in prompt:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "route": "followup",
                            "standalone_query": "guidance on controlling anger at work",
                        }
                    ),
                    provider="fake",
                    model="test",
                )
            return await super().complete(prompt, **kwargs)

    await seed_anger_session(repository, "followup-model")
    routed.router = ConversationRouter(llm=Rewrites())
    # A phrase the lexicon does not hold, so the model path runs.
    result = await routed.handle_turn(
        UserTurn(
            message="but what if he does not listen to any of that",
            session_id="followup-model",
        )
    )

    assert result.route.route == Route.FOLLOWUP.value
    assert result.retrieval is not None
    assert result.retrieval.query == "guidance on controlling anger at work"


async def test_a_lexicon_routed_followup_retrieves_on_the_previous_message(
    routed: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """"aur kya" is decided at zero cost, so there is no rewrite to search for.

    The previous message stands in. The subtlety worth a test is that the
    orchestrator writes the incoming turn to the repository *before* the memory
    stage, so the newest user turn in history is this turn - "the previous
    message" has to skip it explicitly or it resolves to "aur kya" itself.
    """
    await seed_anger_session(repository, "followup-lexicon")
    result = await routed.handle_turn(
        UserTurn(message="aur kya", session_id="followup-lexicon")
    )

    assert result.route.route == Route.FOLLOWUP.value
    assert result.route.stage == RouterDecisionStage.RULES.value
    assert result.route.latency_ms < 50, "the lexicon path must not call a model"
    assert result.retrieval is not None
    assert result.retrieval.query == "boss ne sabke saamne daanta"


async def test_the_vector_leg_searches_the_rewrite_and_fulltext_keeps_the_words(
    routed: GuidanceOrchestrator, repository: InMemoryRepository
) -> None:
    """The two legs get different queries, and the trace records both.

    This is the whole of the query-rewrite change: a Hinglish question scores
    0.00 against an English corpus on the vector leg, while the fulltext leg
    needs the person's exact tokens. Neither is sacrificed for the other.
    """
    import json

    class Rewrites(FakeLLMProvider):
        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            if "TASK: conversation_route" in prompt:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "route": "guidance",
                            "standalone_query": (
                                "My manager takes credit for my work and I want to "
                                "know how to respond."
                            ),
                        }
                    ),
                    provider="fake",
                    model="test",
                )
            return await super().complete(prompt, **kwargs)

    routed.router = ConversationRouter(llm=Rewrites())
    raw = "mera boss mera kaam apne naam kar leta hai"
    result = await routed.handle_turn(UserTurn(message=raw, session_id="rewrite-on"))

    assert result.retrieval is not None
    assert result.retrieval.query == raw, "the fulltext leg keeps their own words"
    assert result.retrieval.vector_query.startswith("My manager takes credit")
    assert result.retrieval.expanded_vector_query


async def test_the_flag_off_restores_the_single_query_path(
    routed: GuidanceOrchestrator,
) -> None:
    """``GITAGUIDE_QUERY_REWRITE=off`` must be byte-identical to before.

    Asserted on the trace rather than on the results: an empty ``vector_query``
    is what makes the retriever take its original single-query path.
    """
    import json

    class Rewrites(FakeLLMProvider):
        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            if "TASK: conversation_route" in prompt:
                return LLMResponse(
                    text=json.dumps(
                        {"route": "guidance", "standalone_query": "an english rewrite"}
                    ),
                    provider="fake",
                    model="test",
                )
            return await super().complete(prompt, **kwargs)

    routed.router = ConversationRouter(llm=Rewrites())
    routed.settings = routed.settings.model_copy(update={"query_rewrite": False})

    result = await routed.handle_turn(
        UserTurn(message="mera boss mujhe pressure deta hai", session_id="rewrite-off")
    )
    assert result.retrieval is not None
    assert result.retrieval.vector_query == ""
    assert result.retrieval.expanded_vector_query == ""
