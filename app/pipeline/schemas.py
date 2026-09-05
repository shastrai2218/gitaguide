"""Shared domain models that travel between pipeline stages.

Research-pipeline role
---------------------
Each stage of GitaGuide consumes and produces one of these typed objects, so a
single request produces a fully inspectable trace:

    UserTurn -> EmotionResult -> IntentResult -> SafetyVerdict
             -> RetrievalTrace -> GenerationResult -> GuidanceResult

The trace is what the frontend "explanation panel" renders and what the
evaluation scripts in ``eval/`` consume, which is why every stage records its
own inputs, scores and the reason it made a decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from corpus.schemas import ScriptureSource, Verse


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------- emotion
class EmotionScore(BaseModel):
    """One GoEmotions label with its sigmoid probability."""

    emotion: str
    score: float = Field(ge=0.0, le=1.0)


class EmotionResult(BaseModel):
    """Output of :class:`app.pipeline.emotion.EmotionDetector`.

    ``intensity`` is *not* the raw model score: it is an arousal-weighted
    aggregate over the top labels (see ``emotion.compute_intensity``) and is the
    value the safety gate thresholds on.
    """

    labels: list[EmotionScore] = Field(default_factory=list)
    intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    backend: str = "unknown"
    latency_ms: float = 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dominant(self) -> str:
        return self.labels[0].emotion if self.labels else "neutral"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dominant_score(self) -> float:
        return self.labels[0].score if self.labels else 0.0

    def top_emotions(self, n: int = 3) -> list[str]:
        return [item.emotion for item in self.labels[:n]]

    def score_of(self, emotion: str) -> float:
        return next((i.score for i in self.labels if i.emotion == emotion), 0.0)


# ---------------------------------------------------------------------- intent
class IntentLabel(str, Enum):
    """The six intents GitaGuide routes on."""

    SEEK_GUIDANCE = "seek_guidance"
    EMOTIONAL_SUPPORT = "emotional_support"
    PROFESSIONAL_DILEMMA = "professional_dilemma"
    SCRIPTURE_QUESTION = "scripture_question"
    CRISIS = "crisis"
    OTHER = "other"


class SemanticStatus(str, Enum):
    """Whether the external semantic intent signal was usable for this turn."""

    NOT_REQUESTED = "not_requested"
    OK = "ok"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"


class SemanticParseStatus(str, Enum):
    """Detailed parser outcome for the semantic intent completion."""

    NOT_ATTEMPTED = "not_attempted"
    PARSED = "parsed"
    INVALID_JSON = "invalid_json"
    INCOMPLETE_SCORES = "incomplete_scores"


class IntentResult(BaseModel):
    """Output of :class:`app.pipeline.intent.IntentClassifier`."""

    intent: IntentLabel = IntentLabel.OTHER
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    scores: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    backend: str = "unknown"
    semantic_status: SemanticStatus = SemanticStatus.NOT_REQUESTED
    semantic_provider: str = ""
    semantic_model: str = ""
    semantic_crisis_score: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_parse_status: SemanticParseStatus = SemanticParseStatus.NOT_ATTEMPTED
    semantic_finish_reason: str = ""
    semantic_prompt_tokens: int = Field(default=0, ge=0)
    semantic_completion_tokens: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------- safety
class SafetyLayer(str, Enum):
    RULE = "rule"
    MODEL = "model"
    OUTPUT = "output"


class SafetyCategory(str, Enum):
    SELF_HARM = "self_harm"
    SUICIDAL_IDEATION = "suicidal_ideation"
    HARM_TO_OTHERS = "harm_to_others"
    ABUSE_OR_VIOLENCE = "abuse_or_violence"
    SEVERE_DISTRESS = "severe_distress"
    MEDICAL_EMERGENCY = "medical_emergency"
    UNSAFE_OUTPUT = "unsafe_output"
    NONE = "none"


class SafetyTrigger(BaseModel):
    """A single reason the gate fired; several may stack in one verdict."""

    layer: SafetyLayer
    category: SafetyCategory
    detail: str
    matched_text: str = ""
    score: float = 0.0


class SafetyVerdict(BaseModel):
    """Output of :class:`app.pipeline.safety.SafetyGate`.

    ``blocked=True`` means the pipeline must short-circuit: no retrieval, no
    generation, return :attr:`response` verbatim.
    """

    blocked: bool = False
    triggers: list[SafetyTrigger] = Field(default_factory=list)
    signals: list[SafetyTrigger] = Field(default_factory=list)
    response: str | None = None
    checked_at: datetime = Field(default_factory=_utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def categories(self) -> list[str]:
        return sorted({t.category.value for t in self.triggers})

    @computed_field  # type: ignore[prop-decorator]
    @property
    def layers(self) -> list[str]:
        return sorted({t.layer.value for t in self.triggers})

    def requires_audit(self) -> bool:
        """True for blocked decisions and meaningful non-blocking signals only."""

        return self.blocked or bool(self.triggers) or bool(self.signals)

    def audit_layers(self) -> list[str]:
        """Layers represented by either blocking triggers or audit signals."""

        return sorted({item.layer.value for item in [*self.triggers, *self.signals]})

    def audit_categories(self) -> list[str]:
        """Categories represented by either blocking triggers or audit signals."""

        return sorted({item.category.value for item in [*self.triggers, *self.signals]})


# ------------------------------------------------------------------- retrieval
class GraphPath(BaseModel):
    """A human-readable justification path taken through the knowledge graph.

    Example::

        GraphPath(nodes=["fear", "BG-2.47"],
                  relationships=["ADDRESSES_EMOTION"],
                  description="fear -[ADDRESSES_EMOTION]- BG-2.47")

    This is what the answer's "Why this verse" section restates, and it is the
    explainability contribution of the project.
    """

    nodes: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    description: str = ""
    hops: int = 0

    @classmethod
    def from_chain(cls, chain: list[str], relationships: list[str]) -> GraphPath:
        parts: list[str] = []
        for index, node in enumerate(chain):
            parts.append(node)
            if index < len(relationships):
                parts.append(f"-[{relationships[index]}]->")
        return cls(
            nodes=chain,
            relationships=relationships,
            description=" ".join(parts),
            hops=max(len(chain) - 1, 0),
        )


class RetrievedVerse(BaseModel):
    """A candidate verse plus every score that decided its rank."""

    verse: Verse
    vector_score: float = 0.0
    fulltext_score: float = 0.0
    graph_score: float = 0.0
    intent_boost: float = 0.0
    final_score: float = 0.0
    retrieval_channels: list[str] = Field(default_factory=list)
    graph_paths: list[GraphPath] = Field(default_factory=list)

    @property
    def verse_id(self) -> str:
        return self.verse.id

    @property
    def source(self) -> ScriptureSource:
        return self.verse.source

    def why(self) -> str:
        """One-line explanation used by the generator and the UI."""
        if self.graph_paths:
            return self.graph_paths[0].description
        channels = ", ".join(self.retrieval_channels) or "vector"
        return f"matched by {channels} similarity"


class RetrievalTrace(BaseModel):
    """Everything the retriever did, kept for the explanation panel."""

    query: str
    expanded_query: str = ""
    emotions_used: list[str] = Field(default_factory=list)
    themes_used: list[str] = Field(default_factory=list)
    candidates_considered: int = 0
    #: Records the database returned that could not be parsed into a Verse.
    #: Non-zero means the query and the parser disagree - see
    #: ``app.pipeline.retrieval._record_to_dict``.
    parse_failures: int = 0
    results: list[RetrievedVerse] = Field(default_factory=list)
    backend: str = "unknown"
    latency_ms: float = 0.0

    @property
    def verse_ids(self) -> list[str]:
        return [item.verse_id for item in self.results]


# ---------------------------------------------------------------------- memory
class MemoryTrace(BaseModel):
    """What stage 4 actually put into the generation prompt.

    Memory was the one stage with no observable output: an empty
    ``memory_summary`` at turn 2 looked identical to a broken memory channel and
    to a working one that simply had nothing to summarise yet. These three
    fields separate those cases, and they are produced by the memory stage
    itself so there is a single source of truth for them.

    ``turn_count`` counts **messages**, not exchanges - the repository
    increments it once per persisted message - so one completed exchange
    advances it by two. It is reported as the pipeline stores it rather than
    halved, because that is the number the ``sessions`` row actually holds.

    ``recent_turns_injected`` is the number of turns handed to
    :meth:`app.pipeline.memory.MemoryContext.render`. The renderer applies a
    character cap afterwards, so under a very long history the prompt may carry
    slightly less than this count.
    """

    turn_count: int = Field(default=0, ge=0)
    summary_included: bool = False
    recent_turns_injected: int = Field(default=0, ge=0)
    #: False when the stage did not run at all - the crisis short-circuit
    #: returns before memory is loaded, by design.
    loaded: bool = False


# ------------------------------------------------------------------ generation
class GenerationResult(BaseModel):
    """Output of :class:`app.pipeline.generation.GroundedGenerator`."""

    answer: str
    citations: list[str] = Field(default_factory=list)
    why_this_verse: dict[str, str] = Field(default_factory=dict)
    grounded: bool = True
    hallucinated_citations: list[str] = Field(default_factory=list)
    #: The provider and model that actually produced this answer, and how far
    #: down the fallback chain it came from (0 = primary). The startup chain
    #: string cannot distinguish "fake is configured as a last resort" from
    #: "fake answered this turn"; these fields can.
    provider: str = "unknown"
    model: str = ""
    fallback_steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


# ----------------------------------------------------------------- top level
class UserTurn(BaseModel):
    """Inbound request as seen by the orchestrator."""

    message: str = Field(min_length=1)
    session_id: str | None = None
    locale: str = "en-IN"
    metadata: dict[str, Any] = Field(default_factory=dict)


class GuidanceResult(BaseModel):
    """The complete, explainable answer returned by the orchestrator."""

    session_id: str
    message_id: str
    answer: str
    crisis: bool = False
    emotion: EmotionResult = Field(default_factory=EmotionResult)
    intent: IntentResult = Field(default_factory=IntentResult)
    safety: SafetyVerdict = Field(default_factory=SafetyVerdict)
    output_safety: SafetyVerdict = Field(default_factory=SafetyVerdict)
    retrieval: RetrievalTrace | None = None
    generation: GenerationResult | None = None
    memory: MemoryTrace = Field(default_factory=MemoryTrace)
    memory_summary: str = ""
    stages_run: list[str] = Field(default_factory=list)
    total_latency_ms: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def citations(self) -> list[str]:
        return self.generation.citations if self.generation else []
