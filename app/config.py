"""Central configuration for GitaGuide.

Research-pipeline role
---------------------
Every module in the pipeline is dual-mode: it either talks to a real backing
service (Neo4j AuraDB, PostgreSQL, an LLM API) or to an in-process fake that
implements the *same interface*.  Which one you get is decided here and nowhere
else, so a researcher can run the full emotion -> safety -> retrieval ->
generation chain on a laptop with no credentials at all.

Set ``GITAGUIDE_MOCK_MODE=true`` (the default) for offline development.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CORPUS_DATA_DIR: Path = PROJECT_ROOT / "corpus" / "data"
SAMPLE_CORPUS_PATH: Path = CORPUS_DATA_DIR / "sample_corpus.json"


class LLMProviderName(str, Enum):
    """Which concrete :class:`app.llm.base.LLMProvider` to instantiate."""

    NVIDIA = "nvidia"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    FAKE = "fake"


class Settings(BaseSettings):
    """Environment-driven settings. Never hardcode secrets; use ``.env``."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_prefix="GITAGUIDE_",
        extra="ignore",
        case_sensitive=False,
        #: Fields with a ``validation_alias`` (nvidia_api_key) must still be
        #: settable by their own name, which is how tests and callers pass them.
        populate_by_name=True,
    )

    # ---------------------------------------------------------------- runtime
    app_name: str = "GitaGuide"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    #: Master offline switch. When true every external dependency is replaced
    #: by an in-memory fake behind the identical interface.
    mock_mode: bool = True

    #: Fine-grained overrides; ``None`` means "follow ``mock_mode``".
    use_real_neo4j: bool | None = None
    use_real_postgres: bool | None = None
    use_real_emotion_model: bool | None = None

    # ------------------------------------------------------------------ neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_vector_index: str = "verseEmbeddingIndex"
    neo4j_fulltext_index: str = "verseTextIndex"

    # --------------------------------------------------------------- postgres
    postgres_dsn: str = "postgresql+asyncpg://gitaguide:gitaguide@localhost:5432/gitaguide"

    # -------------------------------------------------------------- ml models
    emotion_model_id: str = "SamLowe/roberta-base-go_emotions"
    #: ONNX INT8 mirror of the same checkpoint (CPU-only inference).
    emotion_onnx_repo: str = "SamLowe/roberta-base-go_emotions-onnx"
    emotion_onnx_file: str = "onnx/model_quantized.onnx"
    emotion_top_k: int = 5
    emotion_score_threshold: float = 0.05
    onnx_intra_op_threads: int = 2

    embedding_model_id: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # ------------------------------------------------------------------ corpus
    #: Explicit corpus file for the in-memory retriever. ``None`` uses the
    #: search order in ``retrieval._load_default_corpus`` (built corpus first,
    #: bundled sample last). Tests pin this so results do not depend on which
    #: corpus files happen to exist on the machine.
    corpus_path: str | None = None

    # -------------------------------------------------------------------- llm
    llm_provider: LLMProviderName = LLMProviderName.FAKE
    llm_fallback_provider: LLMProviderName | None = None
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    #: Generation defaults (Nemotron 3 answers comfortably inside 1200 tokens).
    llm_max_tokens: int = 1200
    llm_temperature: float = 0.6
    llm_timeout_seconds: float = 60.0

    # ------------------------------------------------------- nvidia nim (llm)
    #: ``NVIDIA_API_KEY`` is accepted unprefixed because that is the name
    #: build.nvidia.com hands out; the prefixed form wins if both are set.
    nvidia_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GITAGUIDE_NVIDIA_API_KEY", "NVIDIA_API_KEY"),
    )
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    #: Verified against GET /v1/models (the catalogue endpoint is public).
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    #: Second NIM model tried before dropping to the fake provider. A different
    #: model means a different rate-limit bucket, which is the point.
    nvidia_fallback_model: str = "nvidia/nemotron-3-nano-30b-a3b"
    nvidia_temperature: float = 0.6
    nvidia_max_tokens: int = 1200
    #: Hard ceiling for intent classification (JSON-only, no prose needed).
    nvidia_intent_max_tokens: int = 100
    #: Nemotron 3 is a hybrid reasoning model; chat answers do not want the trace.
    nvidia_disable_thinking: bool = True
    #: Prepended to the system prompt when thinking is disabled.
    nvidia_thinking_directive: str = "/no_think"
    #: Retries for 429 / 5xx *within* a provider, before failover advances.
    nvidia_max_retries: int = 3
    nvidia_retry_base_delay: float = 1.0
    nvidia_retry_max_delay: float = 20.0

    # ---------------------------------------------------------- knowledge graph
    #: Labels (Theme/Concept) attached to more than this fraction of the corpus
    #: are ignored when building RELATED_TO edges. A label on a quarter of the
    #: corpus carries no signal about relatedness but generates a quadratic
    #: number of pairs; 0.08 sits at the observed knee of the distribution
    #: (governance 9.3% -> hiring 5.9%).
    kg_hub_label_max_fraction: float = 0.08

    # -------------------------------------------------------------- retrieval
    retrieval_top_k: int = 5
    retrieval_candidate_k: int = 20
    graph_expansion_hops: int = 2
    #: Weight applied to a verse whose scripture matches the detected intent
    #: (e.g. Arthashastra for ``professional_dilemma``). See
    #: :func:`app.pipeline.retrieval.rerank`.
    scripture_intent_boost: float = 0.25
    graph_path_boost: float = 0.15

    # ----------------------------------------------------------------- safety
    #: Intensity above which raw emotion (grief/fear) alone trips the gate.
    safety_intensity_threshold: float = 0.85
    #: Crisis-intent probability above which the gate trips.
    safety_crisis_intent_threshold: float = 0.5
    safety_log_enabled: bool = True

    # ----------------------------------------------------------------- memory
    memory_last_n_turns: int = 6
    memory_summary_trigger_turns: int = 8
    memory_summary_max_chars: int = 1200

    # -------------------------------------------------------------------- api
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Allow ``GITAGUIDE_CORS_ORIGINS=a,b`` as well as JSON lists."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # ------------------------------------------------------- derived helpers
    @property
    def neo4j_enabled(self) -> bool:
        if self.use_real_neo4j is not None:
            return self.use_real_neo4j
        return not self.mock_mode and bool(self.neo4j_password)

    @property
    def postgres_enabled(self) -> bool:
        if self.use_real_postgres is not None:
            return self.use_real_postgres
        return not self.mock_mode

    @property
    def emotion_model_enabled(self) -> bool:
        if self.use_real_emotion_model is not None:
            return self.use_real_emotion_model
        return not self.mock_mode

    @property
    def nvidia_enabled(self) -> bool:
        """True when live NIM calls are possible (not offline, key present)."""
        return not self.mock_mode and bool(self.nvidia_api_key)

    @property
    def resolved_llm_provider(self) -> LLMProviderName:
        """Pick the provider that can actually serve, given the credentials.

        Precedence:

        1. mock mode always wins -> ``fake``;
        2. an explicitly chosen provider that has its key;
        3. NVIDIA NIM whenever ``nvidia_api_key`` is set - this makes NIM the
           default as soon as ``GITAGUIDE_MOCK_MODE=false``;
        4. ``fake``, so a missing key degrades instead of crashing.
        """
        if self.mock_mode:
            return LLMProviderName.FAKE

        chosen = self.llm_provider
        has_key = {
            LLMProviderName.NVIDIA: bool(self.nvidia_api_key),
            LLMProviderName.ANTHROPIC: bool(self.anthropic_api_key),
            LLMProviderName.OPENAI: bool(self.openai_api_key),
            LLMProviderName.FAKE: True,
        }
        if chosen is not LLMProviderName.FAKE and has_key[chosen]:
            return chosen
        if self.nvidia_api_key:
            return LLMProviderName.NVIDIA
        return LLMProviderName.FAKE


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (FastAPI dependency friendly)."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cache so tests can monkeypatch environment variables."""
    get_settings.cache_clear()
