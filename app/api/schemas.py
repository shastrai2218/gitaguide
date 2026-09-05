"""Request/response models for the HTTP API.

Kept separate from :mod:`app.pipeline.schemas` so the wire format can change
(pagination, versioning, field trimming) without touching pipeline internals.
The response deliberately carries the *whole* trace: the frontend explanation
panel is a first-class output of this project, not a debug view.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.pipeline.schemas import (
    EmotionResult,
    GraphPath,
    GuidanceResult,
    IntentResult,
    MemoryTrace,
    SafetyVerdict,
)


class ChatRequest(BaseModel):
    """``POST /chat`` body."""

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    locale: str = Field(default="en-IN", max_length=16)

    model_config = {
        "json_schema_extra": {
            "example": {
                "message": "My manager took credit for my work. Should I confront him?",
                "session_id": None,
                "locale": "en-IN",
            }
        }
    }


class VerseOut(BaseModel):
    """One retrieved verse as the explanation panel needs it."""

    id: str
    citation: str
    source: str
    chapter: int
    verse: int
    book: int | None = None
    text_english: str
    text_sanskrit: str = ""
    themes: list[str] = Field(default_factory=list)
    emotions_addressed: list[str] = Field(default_factory=list)

    vector_score: float = 0.0
    fulltext_score: float = 0.0
    graph_score: float = 0.0
    intent_boost: float = 0.0
    final_score: float = 0.0
    channels: list[str] = Field(default_factory=list)
    graph_paths: list[GraphPath] = Field(default_factory=list)
    why: str = ""


class RetrievalOut(BaseModel):
    """Retrieval trace for the UI."""

    query: str
    expanded_query: str = ""
    emotions_used: list[str] = Field(default_factory=list)
    themes_used: list[str] = Field(default_factory=list)
    candidates_considered: int = 0
    backend: str = ""
    latency_ms: float = 0.0
    verses: list[VerseOut] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """``POST /chat`` response - answer plus the full explanation payload."""

    session_id: str
    message_id: str
    answer: str
    crisis: bool = False
    emotion: EmotionResult
    intent: IntentResult
    safety: SafetyVerdict
    output_safety: SafetyVerdict
    retrieval: RetrievalOut | None = None
    citations: list[str] = Field(default_factory=list)
    why_this_verse: dict[str, str] = Field(default_factory=dict)
    grounded: bool = True
    #: What the memory stage injected into this turn's prompt. Produced by the
    #: pipeline, not recomputed here - see :class:`MemoryTrace`.
    memory: MemoryTrace = Field(default_factory=MemoryTrace)
    memory_summary: str = ""
    stages_run: list[str] = Field(default_factory=list)
    total_latency_ms: float = 0.0
    #: Which provider/model actually produced this answer, and how far down the
    #: fallback chain it came from (0 = primary). fallback_steps > 0 means the
    #: answer is degraded; provider "fake" means it is not generated at all.
    llm_provider: str = ""
    llm_model: str = ""
    llm_fallback_steps: int = 0

    @classmethod
    def from_result(cls, result: GuidanceResult) -> ChatResponse:
        """Project a :class:`GuidanceResult` onto the wire format."""
        retrieval: RetrievalOut | None = None
        if result.retrieval is not None:
            retrieval = RetrievalOut(
                query=result.retrieval.query,
                expanded_query=result.retrieval.expanded_query,
                emotions_used=result.retrieval.emotions_used,
                themes_used=result.retrieval.themes_used,
                candidates_considered=result.retrieval.candidates_considered,
                backend=result.retrieval.backend,
                latency_ms=result.retrieval.latency_ms,
                verses=[
                    VerseOut(
                        id=item.verse.id,
                        citation=item.verse.citation,
                        source=item.verse.source.value,
                        chapter=item.verse.chapter,
                        verse=item.verse.verse,
                        book=item.verse.book,
                        text_english=item.verse.text_english,
                        text_sanskrit=item.verse.text_sanskrit,
                        themes=item.verse.themes,
                        emotions_addressed=item.verse.emotions_addressed,
                        vector_score=item.vector_score,
                        fulltext_score=item.fulltext_score,
                        graph_score=item.graph_score,
                        intent_boost=item.intent_boost,
                        final_score=item.final_score,
                        channels=item.retrieval_channels,
                        graph_paths=item.graph_paths,
                        why=item.why(),
                    )
                    for item in result.retrieval.results
                ],
            )

        generation = result.generation
        return cls(
            session_id=result.session_id,
            message_id=result.message_id,
            answer=result.answer,
            crisis=result.crisis,
            emotion=result.emotion,
            intent=result.intent,
            safety=result.safety,
            output_safety=result.output_safety,
            retrieval=retrieval,
            citations=list(generation.citations) if generation else [],
            why_this_verse=dict(generation.why_this_verse) if generation else {},
            grounded=generation.grounded if generation else False,
            memory=result.memory,
            memory_summary=result.memory_summary,
            stages_run=result.stages_run,
            total_latency_ms=result.total_latency_ms,
            llm_provider=generation.provider if generation else "",
            llm_model=generation.model if generation else "",
            llm_fallback_steps=generation.fallback_steps if generation else 0,
        )


class ExplainResponse(BaseModel):
    """``GET /explain/{message_id}`` - the stored trace for one answer."""

    message_id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    crisis: bool = False
    citations: list[str] = Field(default_factory=list)
    emotion: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    safety: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    generation: dict[str, Any] | None = None
    #: Null on rows written before memory observability existed.
    memory: dict[str, Any] | None = None
    latency_ms: float = 0.0


class HealthResponse(BaseModel):
    """``GET /health`` - component-level readiness."""

    status: str = "ok"
    app: str = "GitaGuide"
    version: str = "0.1.0"
    mock_mode: bool = True
    components: dict[str, Any] = Field(default_factory=dict)


class SafetyLogOut(BaseModel):
    """One row of the safety audit log (``GET /safety/logs``)."""

    id: str
    session_id: str | None = None
    message_id: str | None = None
    stage: str
    blocked: bool
    layers: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    dominant_emotion: str = "neutral"
    emotion_intensity: float = 0.0
    intent: str = "other"
    created_at: datetime
