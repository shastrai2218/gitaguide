"""Shared domain models that travel between pipeline stages.

Called by: every pipeline stage; this is the shared vocabulary.

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
    #: Parse outcome of the attempt whose scores were used (the retry, when one
    #: happened).
    semantic_parse_status: SemanticParseStatus = SemanticParseStatus.NOT_ATTEMPTED
    #: Parse outcome of the *first* attempt, kept even when a retry replaced it.
    #: Without it a successful retry would erase the evidence that the model
    #: returns unparseable content at a measurable rate.
    semantic_first_parse_status: SemanticParseStatus = SemanticParseStatus.NOT_ATTEMPTED
    #: True when the first attempt was unusable and a second was made.
    semantic_retried: bool = False
    semantic_finish_reason: str = ""
    semantic_prompt_tokens: int = Field(default=0, ge=0)
    semantic_completion_tokens: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------- safety
class SafetyLayer(str, Enum):
    RULE = "rule"
    MODEL = "model"
    OUTPUT = "output"
    #: Conservative fallback used only when the semantic layer is blind: the
    #: message carries a crisis-adjacent cue, no deterministic rule fired, and
    #: the classifier failed to parse twice. See ``SOFT_CRISIS_CUES``.
    SOFT_CUE_FALLBACK = "soft_cue_fallback"


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
    #: The English rewrite that drove the **vector** leg, when one was used.
    #: Empty means the vector leg searched for ``query`` like the fulltext leg,
    #: which is the case whenever the router produced no rewrite or
    #: ``GITAGUIDE_QUERY_REWRITE`` is off. Both are recorded because a retrieval
    #: that cannot say what it actually searched for cannot be debugged.
    vector_query: str = ""
    expanded_vector_query: str = ""
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
    #: What the output sanity guard did on this turn: ``""`` when the first
    #: completion read as an answer, ``"retried"`` when it was word salad and
    #: the retry was not, ``"shed"`` when both were and the overload reply went
    #: out instead. The detail names the models and the measurements, so a
    #: shed turn can be told apart from a budget-exhausted one in the database.
    quality_guard: str = ""
    quality_guard_detail: str = ""
    #: The register the answer was asked to mirror - "english", "hinglish" or
    #: "hindi", detected from the user's own message
    #: (:mod:`app.pipeline.register`). Recorded so register match can be
    #: measured on real traffic rather than assumed from the prompt.
    #:
    #: Named ``answer_register`` and not ``register`` because pydantic's
    #: metaclass derives from ``ABCMeta``, whose ``register`` classmethod a
    #: field of that name shadows - pydantic warns about it, and a warning
    #: shipped is a warning nobody reads.
    answer_register: str = ""
    #: The conversational route this answer was written for
    #: (:class:`app.pipeline.router.Route`) - "greeting", "guidance",
    #: "followup" and so on. Stored per turn so the reply tiers can be measured
    #: against what each one actually produced, rather than inferred from the
    #: message afterwards. Prefixed like ``answer_register`` for the same
    #: reason: the short name is easy to confuse with the six-class
    #: ``IntentLabel``, which is a different axis and is recorded separately.
    answer_route: str = ""


# ----------------------------------------------------------------- top level
class UserTurn(BaseModel):
    """Inbound request as seen by the orchestrator."""

    message: str = Field(min_length=1)
    session_id: str | None = None
    locale: str = "en-IN"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteTrace(BaseModel):
    """What the conversational router decided for one turn, and how.

    Mirrors :class:`app.pipeline.router.RouteDecision` as a serialisable model.
    It is a mirror rather than the same object because ``router`` imports
    ``memory``, which imports this module - defining the dataclass here would
    close the loop. :meth:`from_decision` is the only place the two are mapped,
    so a field added to one and forgotten in the other shows up immediately.

    ``stage`` is the field worth watching in production: ``rules`` is free,
    ``model`` costs one cheap-tier call, and ``fallback`` means the router could
    not be used and the turn was answered as guidance anyway.
    """

    route: str = "guidance"
    stage: str = "fallback"
    standalone_query: str = ""
    rationale: str = ""
    model: str = ""
    latency_ms: float = 0.0
    fell_back: bool = False

    @classmethod
    def from_decision(cls, decision: Any) -> RouteTrace:
        return cls(
            route=decision.route.value,
            stage=decision.stage.value,
            standalone_query=decision.standalone_query,
            rationale=decision.rationale,
            model=decision.model,
            latency_ms=decision.latency_ms,
            fell_back=decision.fell_back,
        )


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
    #: What the conversational router decided, and how
    #: (:class:`app.pipeline.router.RouteDecision`). ``None`` on a crisis turn
    #: only if the router was never reached - the orchestrator fills in a
    #: gate-stage decision there, so a turn without one means the router stage
    #: did not run at all.
    route: RouteTrace = Field(default_factory=lambda: RouteTrace())
    #: Wall-clock milliseconds per pipeline stage, keyed by the ``STAGE_*``
    #: names in :mod:`app.pipeline.orchestrator`. The copy stored on the message
    #: row omits ``persist`` - a stage cannot time the write that stores it -
    #: which is the same cut the row's own ``latency_ms`` uses.
    timings: dict[str, float] = Field(default_factory=dict)
    memory_summary: str = ""
    stages_run: list[str] = Field(default_factory=list)
    total_latency_ms: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def citations(self) -> list[str]:
        return self.generation.citations if self.generation else []
