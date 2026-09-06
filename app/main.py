"""FastAPI application entry point.

The lifespan handler is where the expensive, once-per-process work happens:
building the repository (PostgreSQL or in-memory), the embedder, the retriever
(Neo4j or in-memory) and the LLM provider, then **warming the emotion model** so
the first user request does not pay for the ONNX session and tokenizer load.

Run locally::

    uvicorn app.main:app --reload            # mock mode, no services needed
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.beta import router as beta_router
from app.api.concurrency import PipelineGate
from app.api.ratelimit import build_rate_limiter
from app.api.routes import router
from app.config import Settings, get_settings
from app.db import build_repository
from app.embeddings import build_embedder
from app.llm.factory import build_llm_provider
from app.pipeline.emotion import EmotionDetector
from app.pipeline.gate_selftest import run_startup_self_test
from app.pipeline.orchestrator import build_orchestrator
from app.pipeline.retrieval import build_retriever
from app.telemetry import build_telemetry

VERSION = "0.1.0"

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the pipeline once, warm the models, tear down cleanly.

    Settings come from ``app.state`` when :func:`create_app` was given some.
    Reading ``get_settings()`` here instead meant the factory argument silently
    configured only CORS while the pipeline, and now the rate limiter, used the
    environment - so a test that passed explicit settings was not testing what
    it appeared to be testing.
    """
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    configure_logging(settings)
    logger.info(
        "starting %s v%s (mock_mode=%s, llm=%s)",
        settings.app_name,
        VERSION,
        settings.mock_mode,
        settings.resolved_llm_provider.value,
    )

    # Before anything expensive, and before the first request can arrive:
    # prove the deterministic safety layer is armed and that no configured
    # value silently disables part of the pipeline. Raising here means the
    # process does not start, which is the point - a GitaGuide with disabled
    # crisis rules, or a budget too small for its own classifier, must not be
    # deployable.
    run_startup_self_test(settings)

    repository = await build_repository(settings)
    embedder = build_embedder(settings)
    llm = build_llm_provider(settings)
    detector = EmotionDetector(settings)
    retriever = build_retriever(settings, embedder)

    orchestrator = build_orchestrator(
        settings=settings,
        repository=repository,
        llm=llm,
        retriever=retriever,
        embedder=embedder,
        detector=detector,
    )

    # Model loading is blocking; keep the event loop free during startup.
    await asyncio.to_thread(orchestrator.warmup)

    app.state.settings = settings
    app.state.orchestrator = orchestrator
    app.state.rate_limiter = build_rate_limiter(settings)
    app.state.telemetry = build_telemetry(settings)
    app.state.pipeline_gate = (
        PipelineGate(settings.pipeline_queue_timeout_seconds)
        if settings.pipeline_serialise
        else None
    )
    if settings.pipeline_serialise:
        logger.info(
            "pipeline serialised: one turn at a time, %.0fs queue timeout "
            "(peak memory is one turn, not N)",
            settings.pipeline_queue_timeout_seconds,
        )
    else:
        logger.warning(
            "PIPELINE SERIALISATION IS OFF; concurrent turns can exceed the "
            "memory limit and be SIGKILLed"
        )
    if settings.telemetry_enabled:
        logger.info("telemetry -> %s (no message text is written)", settings.telemetry_path)
    else:
        logger.warning("TELEMETRY IS DISABLED; the Day 3 residual risk is unobservable")
    if settings.rate_limit_enabled:
        logger.info(
            "rate limits: session=%.0f/min(burst %.0f) ip=%.0f/min(burst %.0f) "
            "global=%.0f/min(burst %.0f) - in-process counters, single worker only",
            settings.rate_limit_session_per_minute,
            settings.rate_limit_session_burst,
            settings.rate_limit_ip_per_minute,
            settings.rate_limit_ip_burst,
            settings.rate_limit_global_per_minute,
            settings.rate_limit_global_burst,
        )
    else:
        logger.warning("RATE LIMITING IS DISABLED; the NIM budget is unprotected")
    logger.info("pipeline ready: %s", await orchestrator.health())

    try:
        yield
    finally:
        await orchestrator.aclose()
        logger.info("shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory (tests build their own instance)."""
    settings = settings or get_settings()
    app = FastAPI(
        title="GitaGuide",
        version=VERSION,
        summary=(
            "Emotion-aware, explainable guidance grounded in the Bhagavad Gita "
            "and the Arthashastra."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Read back by the lifespan handler, so one settings object configures
    # the whole app rather than only the parts create_app touches.
    app.state.settings = settings
    app.include_router(router)
    # The beta UI's flat envelope, served alongside the research surface.
    app.include_router(beta_router)
    return app


app = create_app()
