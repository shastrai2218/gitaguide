"""Shared fixtures. Every test runs fully offline: no weights, no services."""

from __future__ import annotations

import pytest

from app.config import SAMPLE_CORPUS_PATH, Settings, reset_settings_cache
from app.db.repository import InMemoryRepository
from app.embeddings import HashingEmbedder
from app.llm.fake import FakeLLMProvider
from app.pipeline.emotion import EmotionDetector, LexiconEmotionBackend
from app.pipeline.orchestrator import GuidanceOrchestrator, build_orchestrator
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.safety import InMemorySafetyEventSink, SafetyGate
from corpus.schemas import Corpus


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force mock mode regardless of any .env on the developer's machine."""
    monkeypatch.setenv("GITAGUIDE_MOCK_MODE", "true")
    monkeypatch.delenv("GITAGUIDE_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITAGUIDE_OPENAI_API_KEY", raising=False)
    # Overridden rather than deleted: environment variables outrank .env in
    # pydantic-settings, so this guarantees a real NVIDIA key can never reach a
    # test even though .env is present. No test touches the network.
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-unit-test-not-a-real-key")
    monkeypatch.delenv("GITAGUIDE_NVIDIA_API_KEY", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings() -> Settings:
    return Settings(mock_mode=True, environment="test")


@pytest.fixture
def corpus() -> Corpus:
    return Corpus.load(SAMPLE_CORPUS_PATH)


@pytest.fixture
def detector(settings: Settings) -> EmotionDetector:
    """Detector pinned to the lexicon backend (no model download)."""
    return EmotionDetector(settings=settings, backend=LexiconEmotionBackend())


@pytest.fixture
def safety_sink() -> InMemorySafetyEventSink:
    return InMemorySafetyEventSink()


@pytest.fixture
def gate(settings: Settings, safety_sink: InMemorySafetyEventSink) -> SafetyGate:
    return SafetyGate(settings=settings, sink=safety_sink)


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def retriever(corpus: Corpus, settings: Settings) -> InMemoryHybridRetriever:
    return InMemoryHybridRetriever(
        corpus=corpus,
        embedder=HashingEmbedder(dim=settings.embedding_dim),
        settings=settings,
    )


@pytest.fixture
def orchestrator(
    settings: Settings,
    repository: InMemoryRepository,
    llm: FakeLLMProvider,
    retriever: InMemoryHybridRetriever,
    detector: EmotionDetector,
) -> GuidanceOrchestrator:
    return build_orchestrator(
        settings=settings,
        repository=repository,
        llm=llm,
        retriever=retriever,
        detector=detector,
    )
