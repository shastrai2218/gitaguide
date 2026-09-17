"""Unit tests for stage 4 - especially the intent-conditioned re-ranker."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.pipeline.retrieval import (
    FULLTEXT_WEIGHT,
    INTENT_SCRIPTURE_AFFINITY,
    VECTOR_WEIGHT,
    InMemoryHybridRetriever,
    expand_query,
    rerank,
    scripture_affinity,
)
from app.pipeline.schemas import (
    EmotionResult,
    EmotionScore,
    GraphPath,
    IntentLabel,
    IntentResult,
    RetrievedVerse,
)
from corpus.schemas import Corpus, ScriptureSource, Verse


def _verse(verse_id: str, source: ScriptureSource, **kwargs) -> Verse:
    return Verse(
        id=verse_id,
        source=source,
        chapter=kwargs.pop("chapter", 1),
        verse=kwargs.pop("verse", 1),
        book=1 if source is ScriptureSource.ARTHASHASTRA else None,
        text_english=kwargs.pop("text_english", "placeholder text"),
        **kwargs,
    )


def _candidate(
    verse_id: str,
    source: ScriptureSource,
    vector: float = 0.0,
    fulltext: float = 0.0,
    graph: float = 0.0,
) -> RetrievedVerse:
    return RetrievedVerse(
        verse=_verse(verse_id, source),
        vector_score=vector,
        fulltext_score=fulltext,
        graph_score=graph,
    )


# --------------------------------------------------------------- score fusion
def test_final_score_is_the_documented_linear_combination(settings: Settings) -> None:
    candidate = _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, 0.8, 0.5, 0.4)
    (ranked,) = rerank([candidate], IntentLabel.SEEK_GUIDANCE, settings, top_k=1)

    expected = (
        VECTOR_WEIGHT * 0.8
        + FULLTEXT_WEIGHT * 0.5
        + settings.graph_path_boost * 0.4
        + settings.scripture_intent_boost
        * scripture_affinity(IntentLabel.SEEK_GUIDANCE, ScriptureSource.BHAGAVAD_GITA)
    )
    assert ranked.final_score == pytest.approx(expected, abs=1e-6)


def test_results_are_sorted_by_final_score(settings: Settings) -> None:
    candidates = [
        _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, vector=0.2),
        _candidate("BG-2.11", ScriptureSource.BHAGAVAD_GITA, vector=0.9),
        _candidate("BG-6.5", ScriptureSource.BHAGAVAD_GITA, vector=0.5),
    ]
    ranked = rerank(candidates, IntentLabel.SEEK_GUIDANCE, settings)
    assert [item.verse_id for item in ranked] == ["BG-2.11", "BG-6.5", "BG-2.47"]


def test_top_k_truncates(settings: Settings) -> None:
    candidates = [
        _candidate(f"BG-1.{index}", ScriptureSource.BHAGAVAD_GITA, vector=index / 10)
        for index in range(1, 9)
    ]
    assert len(rerank(candidates, IntentLabel.SEEK_GUIDANCE, settings, top_k=3)) == 3


def test_ties_break_deterministically_on_verse_id(settings: Settings) -> None:
    candidates = [
        _candidate("BG-9.1", ScriptureSource.BHAGAVAD_GITA, vector=0.5),
        _candidate("BG-2.1", ScriptureSource.BHAGAVAD_GITA, vector=0.5),
    ]
    first = [item.verse_id for item in rerank(candidates, IntentLabel.OTHER, settings)]
    second = [item.verse_id for item in rerank(list(reversed(candidates)), IntentLabel.OTHER, settings)]
    assert first == second == ["BG-2.1", "BG-9.1"]


# ------------------------------------------------- intent-conditioned weighting
def test_professional_dilemma_promotes_arthashastra(settings: Settings) -> None:
    gita = _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, vector=0.5)
    arthashastra = _candidate("AS-1.9.1", ScriptureSource.ARTHASHASTRA, vector=0.5)
    ranked = rerank([gita, arthashastra], IntentLabel.PROFESSIONAL_DILEMMA, settings)
    assert ranked[0].verse_id == "AS-1.9.1"


def test_emotional_support_promotes_the_gita(settings: Settings) -> None:
    gita = _candidate("BG-2.11", ScriptureSource.BHAGAVAD_GITA, vector=0.5)
    arthashastra = _candidate("AS-1.9.1", ScriptureSource.ARTHASHASTRA, vector=0.5)
    ranked = rerank([gita, arthashastra], IntentLabel.EMOTIONAL_SUPPORT, settings)
    assert ranked[0].verse_id == "BG-2.11"


def test_scripture_question_is_source_neutral(settings: Settings) -> None:
    affinity = INTENT_SCRIPTURE_AFFINITY[IntentLabel.SCRIPTURE_QUESTION]
    assert affinity[ScriptureSource.BHAGAVAD_GITA] == affinity[ScriptureSource.ARTHASHASTRA]


def test_intent_boost_cannot_override_a_large_relevance_gap(settings: Settings) -> None:
    """The prior nudges; it must not beat a strong semantic match."""
    strong_gita = _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, vector=0.95)
    weak_arthashastra = _candidate("AS-1.9.1", ScriptureSource.ARTHASHASTRA, vector=0.1)
    ranked = rerank([strong_gita, weak_arthashastra], IntentLabel.PROFESSIONAL_DILEMMA, settings)
    assert ranked[0].verse_id == "BG-2.47"


def test_zero_boost_setting_disables_the_prior() -> None:
    settings = Settings(mock_mode=True, scripture_intent_boost=0.0)
    candidates = [
        _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, vector=0.5),
        _candidate("AS-1.9.1", ScriptureSource.ARTHASHASTRA, vector=0.5),
    ]
    ranked = rerank(candidates, IntentLabel.PROFESSIONAL_DILEMMA, settings)
    assert all(item.intent_boost == 0.0 for item in ranked)


def test_unknown_intent_gets_the_default_affinity() -> None:
    assert scripture_affinity(IntentLabel.OTHER, ScriptureSource.BHAGAVAD_GITA) > 0


# ------------------------------------------------------------- graph channel
def test_graph_score_lifts_a_weak_vector_match(settings: Settings) -> None:
    weak_with_graph = _candidate("BG-2.11", ScriptureSource.BHAGAVAD_GITA, vector=0.2, graph=1.0)
    weak_without = _candidate("BG-2.14", ScriptureSource.BHAGAVAD_GITA, vector=0.2)
    ranked = rerank([weak_with_graph, weak_without], IntentLabel.EMOTIONAL_SUPPORT, settings)
    assert ranked[0].verse_id == "BG-2.11"


def test_why_prefers_the_graph_path() -> None:
    candidate = _candidate("BG-2.47", ScriptureSource.BHAGAVAD_GITA, vector=0.4)
    candidate.retrieval_channels = ["vector"]
    assert "similarity" in candidate.why()

    candidate.graph_paths = [
        GraphPath.from_chain(["fear", "BG-2.47"], ["ADDRESSES_EMOTION"])
    ]
    assert "ADDRESSES_EMOTION" in candidate.why()


# ------------------------------------------------------ end-to-end retrieval
async def test_emotion_steers_retrieval(retriever: InMemoryHybridRetriever) -> None:
    """A grief message must surface a verse tagged with grief."""
    emotion = EmotionResult(
        labels=[EmotionScore(emotion="grief", score=0.9)], intensity=0.7
    )
    trace = await retriever.retrieve("I lost someone", emotion=emotion)
    assert any("grief" in item.verse.emotions_addressed for item in trace.results)
    assert "graph" in trace.results[0].retrieval_channels


async def test_graph_paths_are_returned_for_explanation(
    retriever: InMemoryHybridRetriever,
) -> None:
    emotion = EmotionResult(labels=[EmotionScore(emotion="anger", score=0.9)], intensity=0.8)
    trace = await retriever.retrieve("my colleague humiliated me", emotion=emotion)
    top = trace.results[0]
    assert top.graph_paths
    assert "ADDRESSES_EMOTION" in top.graph_paths[0].description


async def test_intent_changes_the_ranking(retriever: InMemoryHybridRetriever) -> None:
    query = "I am angry about how work is going"
    emotion = EmotionResult(labels=[EmotionScore(emotion="anger", score=0.8)], intensity=0.7)

    professional = await retriever.retrieve(
        query, emotion, IntentResult(intent=IntentLabel.PROFESSIONAL_DILEMMA, confidence=0.9)
    )
    emotional = await retriever.retrieve(
        query, emotion, IntentResult(intent=IntentLabel.EMOTIONAL_SUPPORT, confidence=0.9)
    )
    assert professional.results[0].source is ScriptureSource.ARTHASHASTRA
    assert emotional.results[0].source is ScriptureSource.BHAGAVAD_GITA


async def test_trace_is_populated(retriever: InMemoryHybridRetriever) -> None:
    trace = await retriever.retrieve("I cannot decide whether to resign")
    assert trace.backend == "in-memory-hybrid"
    assert trace.candidates_considered >= len(trace.results)
    assert trace.expanded_query
    assert trace.latency_ms >= 0


async def test_empty_corpus_returns_no_results(settings: Settings) -> None:
    retriever = InMemoryHybridRetriever(corpus=Corpus(), settings=settings)
    trace = await retriever.retrieve("anything at all")
    assert trace.results == []


def test_expand_query_includes_emotion_and_intent() -> None:
    expanded = expand_query(
        "should I resign",
        EmotionResult(labels=[EmotionScore(emotion="fear", score=0.7)], intensity=0.6),
        IntentResult(intent=IntentLabel.PROFESSIONAL_DILEMMA, confidence=0.8),
    )
    assert "fear" in expanded
    assert "professional dilemma" in expanded


# Phase 1b - the two legs take two queries
async def test_the_vector_leg_and_the_fulltext_leg_get_different_queries(
    corpus: Corpus, settings: Settings
) -> None:
    """The mechanism, asserted at the retriever rather than through a result.

    Checking which verses came back would test the corpus as much as the split.
    What has to hold is narrower and exact: the vector leg is handed the
    rewrite and the fulltext leg is handed the person's own words. Fulltext is
    a lexical match, so substituting a paraphrase throws away the very tokens
    it exists to find; the vector leg wants English because the corpus is
    English.
    """
    retriever = InMemoryHybridRetriever(corpus=corpus, settings=settings)
    seen: dict[str, str] = {}
    original_vector = retriever._vector_scores
    original_fulltext = retriever._fulltext_scores

    def spy_vector(query: str):
        seen["vector"] = query
        return original_vector(query)

    def spy_fulltext(query: str):
        seen["fulltext"] = query
        return original_fulltext(query)

    retriever._vector_scores = spy_vector  # type: ignore[method-assign]
    retriever._fulltext_scores = spy_fulltext  # type: ignore[method-assign]

    await retriever.retrieve(
        "mera boss mera kaam apne naam kar leta hai",
        vector_query="My manager takes credit for my work",
    )

    assert "mera boss" in seen["fulltext"]
    assert "My manager takes credit" in seen["vector"]
    assert "mera boss" not in seen["vector"]


async def test_without_a_rewrite_both_legs_take_the_original_path(
    corpus: Corpus, settings: Settings
) -> None:
    """No rewrite must be byte-identical to the behaviour before rewriting."""
    retriever = InMemoryHybridRetriever(corpus=corpus, settings=settings)
    seen: dict[str, str] = {}
    original_vector = retriever._vector_scores

    def spy_vector(query: str):
        seen["vector"] = query
        return original_vector(query)

    retriever._vector_scores = spy_vector  # type: ignore[method-assign]

    trace = await retriever.retrieve("my manager takes credit for my work")
    assert seen["vector"] == trace.expanded_query
    assert trace.vector_query == ""
    assert trace.expanded_vector_query == ""


async def test_the_trace_records_both_queries(corpus: Corpus, settings: Settings) -> None:
    """A retrieval that cannot say what it searched for cannot be debugged."""
    retriever = InMemoryHybridRetriever(corpus=corpus, settings=settings)
    trace = await retriever.retrieve(
        "mujhe bahut gussa aata hai", vector_query="I get very angry"
    )
    assert trace.query == "mujhe bahut gussa aata hai"
    assert trace.vector_query == "I get very angry"
    assert "I get very angry" in trace.expanded_vector_query
    assert "mujhe bahut gussa" in trace.expanded_query
