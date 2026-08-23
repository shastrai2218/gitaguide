"""Unit tests for stage 5 - grounding and citation enforcement."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.llm.fake import FakeLLMProvider
from app.pipeline.generation import (
    WHY_HEADING,
    GroundedGenerator,
    build_answer_prompt,
    format_context_block,
)
from app.pipeline.memory import MemoryContext
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.schemas import (
    EmotionResult,
    EmotionScore,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
)


@pytest.fixture
async def trace(retriever: InMemoryHybridRetriever) -> RetrievalTrace:
    emotion = EmotionResult(
        labels=[EmotionScore(emotion="nervousness", score=0.8)], intensity=0.6
    )
    return await retriever.retrieve("I am anxious about my appraisal", emotion=emotion)


# ------------------------------------------------------------------- prompting
def test_context_block_has_the_parseable_shape(trace: RetrievalTrace) -> None:
    block = format_context_block(trace.results)
    first = trace.results[0].verse_id
    assert f"[{first}]" in block
    assert "Text:" in block
    assert "Why retrieved:" in block


def test_prompt_lists_allowed_ids(trace: RetrievalTrace) -> None:
    prompt = build_answer_prompt("I am anxious", trace)
    assert "ALLOWED CITATION IDS" in prompt
    for verse_id in trace.verse_ids:
        assert verse_id in prompt


def test_prompt_includes_memory_and_intent(trace: RetrievalTrace) -> None:
    memory = MemoryContext(
        session_id="s1",
        summary="They are deciding whether to resign.",
        recent_turns=[("user", "I am still unsure")],
    )
    prompt = build_answer_prompt(
        "and now?",
        trace,
        intent=IntentResult(intent=IntentLabel.PROFESSIONAL_DILEMMA, confidence=0.9),
        memory=memory,
    )
    assert "deciding whether to resign" in prompt
    assert "professional_dilemma" in prompt


# ------------------------------------------------------------------ generation
async def test_answer_is_grounded_and_cited(
    llm: FakeLLMProvider, settings: Settings, trace: RetrievalTrace
) -> None:
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert result.grounded
    assert result.citations
    assert set(result.citations) <= set(trace.verse_ids)
    assert result.hallucinated_citations == []


async def test_why_this_verse_section_is_present(
    llm: FakeLLMProvider, settings: Settings, trace: RetrievalTrace
) -> None:
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert "why this verse" in result.answer.lower()
    assert set(result.why_this_verse) == set(result.citations)


async def test_missing_why_section_is_appended(
    settings: Settings, trace: RetrievalTrace
) -> None:
    first = trace.verse_ids[0]
    llm = FakeLLMProvider(scripted=[f"Consider {first}: act without clinging to the result."])
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert WHY_HEADING in result.answer
    assert first in result.why_this_verse


async def test_hallucinated_citation_triggers_a_retry(
    settings: Settings, trace: RetrievalTrace
) -> None:
    good = trace.verse_ids[0]
    llm = FakeLLMProvider(
        scripted=[
            "As BG-99.99 teaches, everything is fine.",  # invented id
            f"As {good} teaches, do the work in front of you.",  # corrected
        ]
    )
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert result.citations == [good]
    assert result.grounded
    assert len(llm.calls) == 2, "generator must retry exactly once"


async def test_persistent_hallucination_falls_back_to_a_template(
    settings: Settings, trace: RetrievalTrace
) -> None:
    llm = FakeLLMProvider(
        scripted=["BG-99.99 says so.", "Still BG-98.1, trust me."]
    )
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert result.citations, "template fallback must still cite retrieved verses"
    assert set(result.citations) <= set(trace.verse_ids)
    assert "[citation removed]" not in result.answer


async def test_uncited_answer_triggers_a_retry(
    settings: Settings, trace: RetrievalTrace
) -> None:
    good = trace.verse_ids[0]
    llm = FakeLLMProvider(
        scripted=["Here is some advice with no citation at all.", f"See {good}."]
    )
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert result.citations == [good]


async def test_empty_retrieval_refuses_to_improvise(
    llm: FakeLLMProvider, settings: Settings
) -> None:
    empty = RetrievalTrace(query="obscure question", results=[])
    result = await GroundedGenerator(llm, settings).generate("obscure question", empty)
    assert not result.grounded
    assert result.citations == []
    assert "could not find" in result.answer.lower()
    assert llm.calls == [], "no LLM call should be made without context"


async def test_llm_failure_is_survived(settings: Settings, trace: RetrievalTrace) -> None:
    class Broken(FakeLLMProvider):
        async def complete(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("provider down")

    result = await GroundedGenerator(Broken(), settings).generate("I am anxious", trace)
    assert result.citations, "must fall back to the template answer"
    assert set(result.citations) <= set(trace.verse_ids)
