"""Unit tests for stage 5 - grounding and citation enforcement."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.llm.fake import FakeLLMProvider
from app.pipeline.generation import (
    FOREIGN_ID_PLACEHOLDER,
    WHY_HEADING,
    GroundedGenerator,
    build_answer_prompt,
    format_context_block,
    mask_foreign_verse_ids,
)
from app.pipeline.memory import MemoryContext
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.schemas import (
    EmotionResult,
    EmotionScore,
    GraphPath,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
    RetrievedVerse,
)
from corpus.schemas import ScriptureSource, Verse, extract_verse_ids


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


# --------------------------------------------------------------------------- #
# Graph-path citation leak (regression)
#
# 1-2 hop paths name the *seed* verse, which is often not in the top-k. The
# model would cite it, the enforcer would reject it, and the generator would
# spend a second LLM call on a rewrite - or fall back to the template with
# grounded=False. Ids the model may not cite are masked in the prompt and in the
# answer body; the unmasked path stays in the trace for the explanation panel.
# --------------------------------------------------------------------------- #
LEAKY_SEED = "BG-3.35"


def _leaky_trace() -> RetrievalTrace:
    """A trace whose top result was reached via a 2-hop path from a non-top-k seed."""
    kept = Verse(
        id="BG-18.47",
        source=ScriptureSource.BHAGAVAD_GITA,
        chapter=18,
        verse=47,
        text_english="Better to do your own duty imperfectly than another's well.",
        themes=["duty", "svadharma"],
    )
    other = Verse(
        id="BG-2.47",
        source=ScriptureSource.BHAGAVAD_GITA,
        chapter=2,
        verse=47,
        text_english="Your right is to the work itself, never to its fruits.",
        themes=["duty", "detachment"],
    )
    leaky_path = GraphPath(
        nodes=[LEAKY_SEED, "duty", "BG-18.47"],
        relationships=["HAS_THEME", "HAS_THEME"],
        description=(
            f"({LEAKY_SEED}) -[:HAS_THEME]-> theme 'duty' <-[:HAS_THEME]- (BG-18.47)"
        ),
        hops=2,
    )
    return RetrievalTrace(
        query="should I stay in this job",
        results=[
            RetrievedVerse(
                verse=kept,
                vector_score=0.7,
                graph_score=0.5,
                final_score=0.8,
                retrieval_channels=["vector", "graph"],
                graph_paths=[leaky_path],
            ),
            RetrievedVerse(
                verse=other,
                vector_score=0.6,
                final_score=0.6,
                retrieval_channels=["vector"],
            ),
        ],
    )


@pytest.mark.parametrize(
    ("text", "allowed", "expected"),
    [
        ("(BG-3.35) -> (BG-2.47)", {"BG-2.47"}, f"({FOREIGN_ID_PLACEHOLDER}) -> (BG-2.47)"),
        ("only BG-2.47 here", {"BG-2.47"}, "only BG-2.47 here"),
        ("AS-1.9.1 and AS-2.9.1", {"AS-2.9.1"}, f"{FOREIGN_ID_PLACEHOLDER} and AS-2.9.1"),
        ("matched by vector similarity", {"BG-2.47"}, "matched by vector similarity"),
        ("BG-1.1 BG-1.2", set(), f"{FOREIGN_ID_PLACEHOLDER} {FOREIGN_ID_PLACEHOLDER}"),
    ],
)
def test_mask_foreign_verse_ids(text: str, allowed: set[str], expected: str) -> None:
    assert mask_foreign_verse_ids(text, allowed) == expected


def test_prompt_exposes_only_citable_ids() -> None:
    """The core regression: every id the model can see must be citable."""
    trace = _leaky_trace()
    prompt = build_answer_prompt("should I stay in this job", trace)

    ids_in_prompt = set(extract_verse_ids(prompt))
    assert LEAKY_SEED not in ids_in_prompt, "graph-walk seed leaked into the prompt"
    assert ids_in_prompt <= set(trace.verse_ids)
    assert FOREIGN_ID_PLACEHOLDER in prompt, "the path should still be shown, just masked"


def test_masking_keeps_the_path_readable() -> None:
    block = format_context_block(_leaky_trace().results)
    why_line = next(line for line in block.splitlines() if line.startswith("Why retrieved:"))
    assert "HAS_THEME" in why_line, "the relationship structure must survive masking"
    assert "duty" in why_line
    assert LEAKY_SEED not in why_line


def test_trace_keeps_the_unmasked_path_for_the_panel() -> None:
    """Masking is prompt/answer-only: the explanation panel needs the real path."""
    trace = _leaky_trace()
    format_context_block(trace.results)  # must not mutate

    top = trace.results[0]
    assert LEAKY_SEED in top.graph_paths[0].description
    assert LEAKY_SEED in top.graph_paths[0].nodes
    assert LEAKY_SEED in top.why()


async def test_leaky_path_no_longer_costs_a_retry(
    llm: FakeLLMProvider, settings: Settings
) -> None:
    """The money assertion: one LLM call per turn, not two."""
    trace = _leaky_trace()
    result = await GroundedGenerator(llm, settings).generate("should I stay in this job", trace)

    assert len(llm.calls) == 1, "a masked prompt must not trigger the corrective rewrite"
    assert result.grounded
    assert result.hallucinated_citations == []
    assert set(result.citations) <= set(trace.verse_ids)


async def test_answer_body_carries_no_uncitable_id(
    llm: FakeLLMProvider, settings: Settings
) -> None:
    trace = _leaky_trace()
    result = await GroundedGenerator(llm, settings).generate("should I stay in this job", trace)

    assert LEAKY_SEED not in result.answer
    assert set(extract_verse_ids(result.answer)) <= set(trace.verse_ids)
    assert all(LEAKY_SEED not in why for why in result.why_this_verse.values())


async def test_template_fallback_is_also_clean(settings: Settings) -> None:
    """Even the deterministic fallback answer must not name a seed verse."""
    stubborn = FakeLLMProvider(scripted=["BG-99.99 only.", "BG-98.1 only."])
    trace = _leaky_trace()
    result = await GroundedGenerator(stubborn, settings).generate("q", trace)

    assert LEAKY_SEED not in result.answer
    assert set(extract_verse_ids(result.answer)) <= set(trace.verse_ids)


# --------------------------------------------------------------------------- #
# Arthashastra ids are three-part (AS-<book>.<chapter>.<sutra>) while Gita ids
# are two-part (BG-<chapter>.<verse>). The citation regex and the masker were
# written when only BG existed, so both forms are pinned here explicitly.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cite AS-1.2.3 here", ["AS-1.2.3"]),
        ("AS-15.1.39 and BG-2.47", ["AS-15.1.39", "BG-2.47"]),
        ("(AS-7.1.2)", ["AS-7.1.2"]),
        ("AS-1.19.34.", ["AS-1.19.34"]),
        ("AS-2.36.29 mid-sentence", ["AS-2.36.29"]),
        ("not an id: AS-1", []),
    ],
)
def test_extract_verse_ids_handles_three_part_as_ids(text: str, expected: list[str]) -> None:
    assert extract_verse_ids(text) == expected


@pytest.mark.parametrize(
    ("text", "allowed", "expected"),
    [
        # A suppressed AS id is masked, a permitted one survives.
        (
            "(AS-1.9.1) -[:HAS_THEME]-> theme 'hiring' <-[:HAS_THEME]- (AS-2.9.1)",
            {"AS-2.9.1"},
            f"({FOREIGN_ID_PLACEHOLDER}) -[:HAS_THEME]-> theme 'hiring' "
            "<-[:HAS_THEME]- (AS-2.9.1)",
        ),
        # Mixed corpora in one line.
        (
            "AS-2.9.1 and BG-2.47 are fine; AS-15.1.39 is not",
            {"AS-2.9.1", "BG-2.47"},
            f"AS-2.9.1 and BG-2.47 are fine; {FOREIGN_ID_PLACEHOLDER} is not",
        ),
        # Book number must not be confused with a chapter: AS-1.9.1 != AS-9.1.
        ("AS-1.9.1", {"AS-9.1"}, FOREIGN_ID_PLACEHOLDER),
        ("AS-1.9.1", {"AS-1.9.1"}, "AS-1.9.1"),
    ],
)
def test_mask_foreign_verse_ids_handles_as_ids(
    text: str, allowed: set[str], expected: str
) -> None:
    assert mask_foreign_verse_ids(text, allowed) == expected


def _leaky_as_trace() -> RetrievalTrace:
    """An Arthashastra trace whose graph path names a non-retrieved AS seed."""
    kept = Verse(
        id="AS-2.9.1",
        source=ScriptureSource.ARTHASHASTRA,
        book=2,
        chapter=9,
        verse=1,
        text_english="Superintendents shall be appointed with regard to their work, and audited often.",
        themes=["accountability", "hiring"],
    )
    path = GraphPath(
        nodes=["AS-1.9.1", "hiring", "AS-2.9.1"],
        relationships=["HAS_THEME", "HAS_THEME"],
        description=(
            "(AS-1.9.1) -[:HAS_THEME]-> theme 'hiring' <-[:HAS_THEME]- (AS-2.9.1)"
        ),
        hops=2,
    )
    return RetrievalTrace(
        query="whom should I trust with responsibility",
        results=[
            RetrievedVerse(
                verse=kept,
                vector_score=0.7,
                graph_score=0.5,
                final_score=0.8,
                retrieval_channels=["vector", "graph"],
                graph_paths=[path],
            )
        ],
    )


def test_as_prompt_exposes_only_citable_ids() -> None:
    trace = _leaky_as_trace()
    prompt = build_answer_prompt("whom should I trust", trace)
    assert "AS-1.9.1" not in prompt, "AS graph-walk seed leaked into the prompt"
    assert set(extract_verse_ids(prompt)) == {"AS-2.9.1"}


async def test_as_citation_survives_enforcement(
    llm: FakeLLMProvider, settings: Settings
) -> None:
    """A three-part AS id must pass the citation check, not be stripped."""
    trace = _leaky_as_trace()
    result = await GroundedGenerator(llm, settings).generate("whom should I trust", trace)
    assert result.grounded
    assert result.citations == ["AS-2.9.1"]
    assert result.hallucinated_citations == []
    assert len(llm.calls) == 1, "no corrective retry should be needed"
