"""The generation wall-clock budget, and the overload turn at the end of it.

A tester waiting on a spinner cannot tell a slow model from a broken one, and
neither can a 500. The budget makes the wait bounded and the outcome legible:
the primary's retries have to finish inside it, the fallback gets what is left,
and when nothing is left the reply says so in the same shape as any other turn.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.llm.base import (
    BudgetExhaustedError,
    FallbackLLMProvider,
    LLMError,
    LLMProvider,
    LLMResponse,
)
from app.pipeline.generation import (
    OVERLOADED_ANSWER,
    PROVIDER_OVERLOADED,
    GroundedGenerator,
)
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.schemas import RetrievalTrace


class SlowProvider(LLMProvider):
    """Answers, eventually. Stands in for a model under load."""

    def __init__(self, name: str, delay: float) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.delay = delay
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> LLMResponse:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return LLMResponse(text="answered", provider=self.name, model=self.model)


class BrokenProvider(LLMProvider):
    """Fails immediately, the way an unconfigured or 4xx provider does."""

    def __init__(self, name: str = "broken") -> None:
        self.name = name
        self.model = f"{name}-model"
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> LLMResponse:
        self.calls += 1
        raise LLMError("nope")


# The chain
async def test_a_fast_primary_answers_and_the_fallback_is_untouched() -> None:
    primary, fallback = SlowProvider("primary", 0.01), SlowProvider("fallback", 0.01)
    chain = FallbackLLMProvider([primary, fallback], budget_seconds=5.0)

    response = await chain.complete("hello")
    assert response.provider == "primary"
    assert response.raw["fallback_steps"] == 0
    assert fallback.calls == 0


async def test_a_slow_primary_is_cut_off_and_the_fallback_answers() -> None:
    """This is the Super -> Lightning handover, in miniature."""
    primary, fallback = SlowProvider("primary", 5.0), SlowProvider("fallback", 0.01)
    chain = FallbackLLMProvider([primary, fallback], budget_seconds=0.3)

    response = await chain.complete("hello")
    assert response.provider == "fallback"
    assert response.raw["fallback_steps"] == 1


async def test_the_budget_spans_the_chain_rather_than_each_provider() -> None:
    """Two providers that are each 'reasonable' must not add up to a long wait."""
    providers = [SlowProvider("a", 5.0), SlowProvider("b", 5.0)]
    chain = FallbackLLMProvider(providers, budget_seconds=0.3)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(BudgetExhaustedError):
        await chain.complete("hello")
    elapsed = loop.time() - started

    assert elapsed < 1.5, f"the chain waited {elapsed:.1f}s against a 0.3s budget"


async def test_budget_exhaustion_is_distinguishable_from_everything_failing() -> None:
    """The honest reply differs, so the exception has to differ too."""
    broken = FallbackLLMProvider([BrokenProvider(), BrokenProvider("also")], budget_seconds=5.0)
    with pytest.raises(LLMError) as failure:
        await broken.complete("hello")
    assert not isinstance(failure.value, BudgetExhaustedError)

    slow = FallbackLLMProvider([SlowProvider("slow", 5.0)], budget_seconds=0.2)
    with pytest.raises(BudgetExhaustedError):
        await slow.complete("hello")


async def test_no_budget_configured_means_no_deadline() -> None:
    chain = FallbackLLMProvider([SlowProvider("p", 0.01)], budget_seconds=None)
    assert (await chain.complete("hello")).provider == "p"


# The overload turn
async def test_an_exhausted_budget_produces_a_normal_shaped_overload_turn(
    retriever: InMemoryHybridRetriever,
) -> None:
    """Not a 500, and not a template answer passed off as guidance."""
    trace: RetrievalTrace = await retriever.retrieve("I am anxious about my appraisal")
    assert trace.results, "the fixture corpus should retrieve something"

    generator = GroundedGenerator(
        llm=FallbackLLMProvider([SlowProvider("slow", 5.0)], budget_seconds=0.2),
        settings=Settings(mock_mode=True),
    )
    result = await generator.generate("I am overworked", trace)

    assert result.answer == OVERLOADED_ANSWER
    assert result.provider == PROVIDER_OVERLOADED
    assert result.citations == []
    assert result.grounded is False
    assert "overloaded" in result.answer.lower()


async def test_the_overload_turn_cites_nothing() -> None:
    """Attaching scripture to a turn the model never answered would be a lie."""
    assert "BG-" not in OVERLOADED_ANSWER
    assert "AS-" not in OVERLOADED_ANSWER
