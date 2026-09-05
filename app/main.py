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
from app.api.routes import router
from app.config import Settings, get_settings
from app.db import build_repository
from app.embeddings import build_embedder
from app.llm.factory import build_llm_provider
from app.pipeline.emotion import EmotionDetector
from app.pipeline.orchestrator import build_orchestrator
from app.pipeline.retrieval import build_retriever

VERSION = "0.1.0"

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the pipeline once, warm the models, tear down cleanly."""
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "starting %s v%s (mock_mode=%s, llm=%s)",
        settings.app_name,
        VERSION,
        settings.mock_mode,
        settings.resolved_llm_provider.value,
    )

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
    app.include_router(router)
    # The beta UI's flat envelope, served alongside the research surface.
    app.include_router(beta_router)
    return app


app = create_app()
