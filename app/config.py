"""Central configuration for GitaGuide.

Called by: everything; the only place backends are chosen.

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

#: The consent notice testers are currently asked to accept.
#:
#: This is the version printed on ``docs/tester-pack/PRIVACY.md`` and
#: ``DISCLAIMER.md``, the version the frontend sends (``CONSENT_VERSION`` in
#: ``frontend/src/api.js``), and the version ``/api/consent`` records. One
#: string in four places; ``tests/test_tester_pack.py`` holds them together.
#: A session that consented to any earlier version is asked again.
CURRENT_CONSENT_VERSION = "beta-1"


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
    #: fp32 ONNX export of the embedding model, run on onnxruntime so the
    #: serving runtime needs no torch. fp32 on purpose: the Neo4j index was
    #: built with the fp32 encoder and a quantised query vector would not be
    #: commensurate with it.
    embedding_onnx_file: str = "onnx/model.onnx"
    #: ``max_seq_length`` from the model's sentence_bert_config.json.
    embedding_max_seq_length: int = 256
    #: INT8 export of the *same* MiniLM. Not a different model - the same
    #: weights, quantised. ``quint8_avx2`` rather than an AVX-512 build because
    #: it runs on any x86-64 host; the AVX-512 file is ~12 MiB smaller but only
    #: loads where that instruction set is present.
    embedding_onnx_file_int8: str = "onnx/model_quint8_avx2.onnx"
    #: Texts per forward pass.
    #:
    #: The hidden-state tensor is ``batch x tokens x 384`` float32, so an
    #: unbatched call scales with the caller's list: embedding the 3,836-verse
    #: corpus in one pass asks for 1.4 GiB in a single allocation and raises.
    #: 64 keeps a worst-case batch near 25 MiB, which the 512 MB instance can
    #: hold alongside everything else. Serving embeds one query at a time, so
    #: this only binds on bulk paths like scripts/reembed_graph.py.
    embedding_batch_size: int = 64
    #: Which export to load: ``fp32`` or ``int8``.
    #:
    #: **The index and the query encoder must match.** Vectors are compared by
    #: cosine, so querying an fp32-built index with an INT8 encoder silently
    #: degrades retrieval - measured at 0.986 min cosine drift, which is not an
    #: error anywhere, just worse neighbours. Both sides using the same INT8
    #: encoder is self-consistent and has no such penalty; that is why this is
    #: one switch rather than two.
    #:
    #: Default stays fp32 so local development and the existing local graph are
    #: unchanged. Production sets ``GITAGUIDE_EMBEDDER_PRECISION=int8`` because
    #: the free tier cannot hold the fp32 encoder (see render.yaml).
    embedder_precision: Literal["fp32", "int8"] = "fp32"

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
    nvidia_fallback_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    nvidia_temperature: float = 0.6
    nvidia_max_tokens: int = 1200
    #: Hard ceiling for intent classification (JSON-only, no prose needed).
    nvidia_intent_max_tokens: int = 128
    #: Nemotron 3 is a hybrid reasoning model; chat answers do not want the trace.
    nvidia_disable_thinking: bool = True
    #: Prepended to the system prompt when thinking is disabled.
    nvidia_thinking_directive: str = "/no_think"
    #: Retries for 429 / 5xx *within* a provider, before failover advances.
    nvidia_max_retries: int = 3
    nvidia_retry_base_delay: float = 1.0
    nvidia_retry_max_delay: float = 20.0

    # ------------------------------------------------- conversational router
    #: Turns the stage-2 router model off, leaving only the zero-cost lexicon.
    #: With it false every message the lexicon does not decide is treated as
    #: guidance - which is exactly how the pipeline behaved before Phase 1b, so
    #: this is the one-env-var way back if routing misbehaves in production.
    router_model_enabled: bool = True
    #: The cheap tier. Routing is a labelling task with a fixed output shape, so
    #: it does not need the 120B model, and putting it on a *different* model
    #: also puts it in a different NIM rate-limit bucket from generation.
    router_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    #: Hard wall-clock ceiling for the router call. Past this the turn is routed
    #: to guidance and answered normally: a slow router must cost latency, never
    #: an answer.
    #:
    #: Raised 4.0 -> 6.0 on 13 Sep 2026. At 4s the measured router was accurate
    #: but kept missing its own deadline: 4 of 26 model-stage calls were
    #: cancelled at the ceiling, and 3 of the 5 routing errors in the 40-message
    #: set were timeouts rather than misjudgements (section 14b). The returning
    #: calls sat at a median of 938ms, so the distribution is bimodal and the
    #: extra 2s is spent only on the tail.
    router_timeout_seconds: float = 6.0
    #: The reply is one small JSON object; anything longer is malformed.
    router_max_tokens: int = 160

    #: Embed the router's English rewrite instead of the raw message on the
    #: **vector** leg of retrieval. The fulltext leg always keeps the raw
    #: message, so the person's own vocabulary is never lost.
    #:
    #: Why this exists: on the encoder in production, the same question scores
    #: 0.30 in English and 0.00 in Hinglish (section 14b). Neither NIM
    #: multilingual encoder fixes that - both also score 0.00 - so the gap is
    #: not an encoder choice, it is that the corpus is English and the query is
    #: not. Rewriting the query is the one intervention that works on the
    #: encoder already deployed, with no index rebuild.
    #:
    #: Accepts on/off as well as true/false. Off restores the pre-Phase-1b
    #: behaviour of embedding the raw message, in one env var and no deploy.
    query_rewrite: bool = True


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
    #: Retained calibration marker for emotion evaluation; it does not block alone.
    safety_intensity_threshold: float = 0.85
    #: Crisis-intent probability above which the gate trips.
    safety_crisis_intent_threshold: float = 0.5
    safety_log_enabled: bool = True

    # ----------------------------------------------------------------- memory
    memory_last_n_turns: int = 6
    memory_summary_trigger_turns: int = 8
    memory_summary_max_chars: int = 1200

    # -------------------------------------------------------------------- api
    # ------------------------------------------------------ pipeline admission
    #: Serialise turns so peak memory is one turn rather than N.
    #:
    #: Required on a 512 MB instance: one turn fits with a small margin, two do
    #: not, and exceeding it is a SIGKILL rather than an error. Off only for
    #: tests that need concurrency and are not memory-bound.
    pipeline_serialise: bool = True
    #: How long a turn may wait for the pipeline before being shed with the
    #: overload reply. A turn is 10-35s, so one queued turn resolves inside the
    #: UI's patience copy; beyond this the tester is better served by an honest
    #: "try again" than by a queue they cannot see.
    pipeline_queue_timeout_seconds: float = 30.0

    # --------------------------------------------------- output sanity guard
    #: Fewest words the answer body (before "Why this verse") may have and
    #: still count as an answer. The system prompt asks for 180-320; the
    #: shortest legitimate production reply seen is ~45. The degenerate reply
    #: served on 8 Sep 2026 had 21, so this floor alone would not have caught
    #: it - the stutter check in ``generation.assess_answer`` did. The floor
    #: catches the fragments ("I the I the") that a stutter check on four
    #: words cannot.
    output_guard_min_words: int = 12
    #: ``1 - distinct/total`` over the body's words. Real prose of 100-300
    #: words sits near 0.4-0.5; a loop ("I the I the ...") goes to 0.9+.
    output_guard_max_repetition: float = 0.6

    # ------------------------------------------------------- generation budget
    #: Total wall clock allowed for one turn's generation, across the whole
    #: fallback chain. The Day 3 worst observed turn was 14.4s and a later UI
    #: turn reached 29.7s, so 45s is generous - it is a stop, not a target.
    #: When it runs out the tester gets an honest "overloaded" reply rather
    #: than a spinner that never ends or a 500.
    generation_budget_seconds: float = 45.0

    # -------------------------------------------------------------- telemetry
    #: One JSON line per turn, to a rotating local file. Never message text.
    telemetry_enabled: bool = True
    telemetry_path: str = "logs/telemetry.jsonl"
    telemetry_max_bytes: int = 10_000_000
    telemetry_backup_count: int = 5
    #: Salt for the session pseudonym. Change it per deployment; changing it
    #: makes old and new logs ungroupable, which is the intended trade.
    telemetry_session_salt: str = "gitaguide-beta"

    # ---------------------------------------------------------------- consent
    #: The consent text the tester agreed to. Bump when the wording changes in a
    #: way a tester would want to see again; a session consented to an older
    #: version is treated as not consented. ``v1`` was the pre-launch
    #: placeholder; ``beta-1`` is the notice the tester pack carries.
    consent_version: str = CURRENT_CONSENT_VERSION
    #: Server-side enforcement. Off only for tests that are not about consent -
    #: never in anything a tester touches, or the record is worthless.
    consent_required: bool = True

    # ------------------------------------------------------------ rate limits
    #: NIM free tier is ~40 req/min *per model* and one normal turn spends two
    #: calls on the primary model (intent + generation), ~2.05 with the
    #: malformed-intent retry. That caps the whole deployment near 19 turns/min,
    #: so the global bucket is the one that actually protects the budget -
    #: per-session and per-IP limits only stop one tester monopolising it.
    rate_limit_enabled: bool = True
    #: How many trusted reverse proxies sit between the client and this process.
    #:
    #: Controls how ``X-Forwarded-For`` is read for the per-IP limit. The header
    #: is client-supplied and therefore forgeable, so the default is **0: do not
    #: trust it at all** and use the socket peer. A deployment behind exactly one
    #: platform proxy - Render, Fly, most PaaS - sets this to 1, which reads the
    #: entry that proxy appended rather than anything the client wrote.
    #:
    #: Setting it higher than the real number of proxies lets a client choose
    #: its own rate-limit bucket by forging earlier entries, so it must match
    #: the deployment rather than being set generously.
    rate_limit_trusted_proxy_hops: int = 0
    #: Per session: comfortable for a person thinking between messages.
    rate_limit_session_per_minute: float = 5.0
    rate_limit_session_burst: float = 3.0
    #: Per IP, set well above the session limit because testers in one office
    #: share a NAT address and must not rate-limit each other.
    rate_limit_ip_per_minute: float = 20.0
    rate_limit_ip_burst: float = 8.0
    #: Global: 16/min against a ~19/min ceiling leaves headroom for retries and
    #: the occasional summary call.
    rate_limit_global_per_minute: float = 16.0
    rate_limit_global_burst: float = 6.0

    # -------------------------------------------------------------------- auth
    #: Clerk's Frontend API origin for this application, e.g.
    #: ``https://your-app.clerk.accounts.dev`` (or a custom domain on a paid
    #: plan). This is also the JWT ``iss`` claim Clerk issues, and
    #: ``{clerk_issuer}/.well-known/jwks.json`` is where its signing keys live.
    #: Empty by default: auth is optional everywhere it appears, and a session
    #: with no token attached simply stays anonymous, the way every session
    #: worked before this existed.
    clerk_issuer: str = ""

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
    def auth_enabled(self) -> bool:
        """True once a Clerk issuer is configured. False leaves every session anonymous."""
        return bool(self.clerk_issuer)

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
