"""Shared fixtures. Every test runs fully offline: no weights, no services."""

from __future__ import annotations

import os

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

#: Environment pinned for the whole test session. Applied in
#: :func:`pytest_configure` - i.e. before *any* fixture, including
#: module/session-scoped ones - because a module-scoped app fixture
#: (``tests/test_api.py``) is built before function-scoped fixtures run, and it
#: was therefore reaching a live AuraDB instance through the developer's
#: exported credentials. A test suite that can touch a real database is a test
#: suite that can hang, mutate data, or pass for the wrong reason.
OFFLINE_ENV: dict[str, str] = {
    "GITAGUIDE_MOCK_MODE": "true",
    "GITAGUIDE_USE_REAL_NEO4J": "false",
    "GITAGUIDE_USE_REAL_POSTGRES": "false",
    "GITAGUIDE_USE_REAL_EMOTION_MODEL": "false",
    "GITAGUIDE_LLM_PROVIDER": "fake",
    # Point at localhost so an accidental connection is refused immediately
    # rather than hanging on a remote host, and carry no usable credentials.
    "GITAGUIDE_NEO4J_URI": "bolt://localhost:7687",
    "GITAGUIDE_NEO4J_PASSWORD": "",
    "GITAGUIDE_POSTGRES_DSN": "postgresql+asyncpg://test:test@localhost:5432/test",
    "NVIDIA_API_KEY": "nvapi-unit-test-not-a-real-key",
    # Pin the 16-verse sample: a locally built 701-verse corpus/data/gita.json
    # would otherwise change what the app-level tests retrieve.
    "GITAGUIDE_CORPUS_PATH": "corpus/data/sample_corpus.json",
    # The developer's .env sets this to int8 to match the local graph, and
    # Settings reads .env. Tests that want INT8 ask for it explicitly; the rest
    # must see the code default rather than whatever the local graph needs.
    "GITAGUIDE_EMBEDDER_PRECISION": "fp32",
}


def pytest_configure(config: pytest.Config) -> None:
    """Pin the offline environment before collection or any fixture runs."""
    for name, value in OFFLINE_ENV.items():
        os.environ[name] = value
    for name in ("GITAGUIDE_NVIDIA_API_KEY", "GITAGUIDE_ANTHROPIC_API_KEY",
                 "GITAGUIDE_OPENAI_API_KEY"):
        os.environ.pop(name, None)

@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-assert the offline pins per test and clear the settings cache.

    :func:`pytest_configure` already pinned the environment for the session;
    this keeps it true for tests that mutate ``os.environ`` themselves, and
    makes each test see freshly parsed settings.
    """
    for name, value in OFFLINE_ENV.items():
        monkeypatch.setenv(name, value)
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
