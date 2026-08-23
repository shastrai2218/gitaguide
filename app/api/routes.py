"""HTTP surface: ``POST /chat``, ``GET /explain/{message_id}``, ``GET /health``.

The routes are deliberately thin - they translate HTTP to
:class:`app.pipeline.schemas.UserTurn` and back. All pipeline decisions
(including the safety short-circuit) live in the orchestrator, so the CLI, the
evaluation scripts and the API all exercise identical logic.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ExplainResponse,
    HealthResponse,
    SafetyLogOut,
)
from app.config import Settings, get_settings
from app.pipeline.orchestrator import GuidanceOrchestrator
from app.pipeline.schemas import UserTurn

logger = logging.getLogger(__name__)

router = APIRouter()


def get_orchestrator(request: Request) -> GuidanceOrchestrator:
    """Pull the singleton built by the lifespan handler in :mod:`app.main`."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:  # pragma: no cover - only if startup failed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="pipeline is not ready",
        )
    return orchestrator


OrchestratorDep = Annotated[GuidanceOrchestrator, Depends(get_orchestrator)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    payload: ChatRequest,
    orchestrator: OrchestratorDep,
) -> ChatResponse:
    """Run one turn of the pipeline and return the answer plus its full trace.

    A crisis turn returns HTTP 200 with ``crisis=true`` and no retrieval trace:
    the gate short-circuits before retrieval and generation, by design.
    """
    turn = UserTurn(
        message=payload.message,
        session_id=payload.session_id,
        locale=payload.locale,
    )
    try:
        result = await orchestrator.handle_turn(turn)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("pipeline failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the guidance pipeline failed; please try again",
        ) from exc
    return ChatResponse.from_result(result)


@router.get("/explain/{message_id}", response_model=ExplainResponse, tags=["chat"])
async def explain(message_id: str, orchestrator: OrchestratorDep) -> ExplainResponse:
    """Return the stored pipeline trace for a previously generated answer."""
    record = await orchestrator.explain(message_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
    return ExplainResponse(
        message_id=record.id,
        session_id=record.session_id,
        role=record.role,
        content=record.content,
        created_at=record.created_at,
        crisis=record.crisis,
        citations=list(record.citations or []),
        emotion=record.emotion,
        intent=record.intent,
        safety=record.safety,
        retrieval=record.retrieval,
        generation=record.generation,
        latency_ms=record.latency_ms,
    )


@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health(orchestrator: OrchestratorDep, settings: SettingsDep) -> HealthResponse:
    """Component-level health, including which backends are actually in use."""
    components = await orchestrator.health()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        mock_mode=settings.mock_mode,
        components=components,
    )


@router.get("/safety/logs", response_model=list[SafetyLogOut], tags=["ops"])
async def safety_logs(
    orchestrator: OrchestratorDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[SafetyLogOut]:
    """Recent gate triggers - the audit trail used for evaluating the gate."""
    records = await orchestrator.repository.list_safety_logs(limit=limit)
    return [
        SafetyLogOut(
            id=record.id,
            session_id=record.session_id,
            message_id=record.message_id,
            stage=record.stage,
            blocked=record.blocked,
            layers=record.layers,
            categories=record.categories,
            dominant_emotion=record.dominant_emotion,
            emotion_intensity=record.emotion_intensity,
            intent=record.intent,
            created_at=record.created_at,
        )
        for record in records
    ]
