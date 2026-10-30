"""Stage 4 - hybrid retrieval (vector + full-text + graph expansion).

Called by: app.pipeline.orchestrator, stage 5.

Research-pipeline role
---------------------
This is the retrieval contribution of the project. Three channels are fused:

``vector``    cosine similarity over verse embeddings (semantic paraphrase),
``fulltext``  idf-weighted lexical overlap (proper nouns, exact phrases),
``graph``     1-2 hop walks over ``ADDRESSES_EMOTION`` / ``HAS_THEME`` /
              ``RELATED_TO``, seeded by the *detected emotions* and by the top
              vector hits - this is what lets affect (not just wording) steer
              which verse is surfaced, and it produces the :class:`GraphPath`
              objects that the answer's "Why this verse" section restates.

The fused candidates are then re-ranked with an intent-conditioned scripture
prior (:data:`INTENT_SCRIPTURE_AFFINITY`): a professional dilemma pulls the
Arthashastra up, emotional support pulls the Gita up. :func:`rerank` is a pure
function so it can be unit-tested without any backing store.

Two implementations, one interface
----------------------------------
``InMemoryHybridRetriever``  the offline default; the whole graph lives in dicts.
``Neo4jHybridRetriever``     production; ``neo4j-graphrag``'s
                            ``HybridCypherRetriever`` with the tail query in
                            :data:`kg.cypher_queries.HYBRID_RETRIEVAL_QUERY`.
"""

from __future__ import annotations

import ast
import inspect
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.embeddings import Embedder, build_embedder, cosine_similarity, tokenize
from app.pipeline.schemas import (
    EmotionResult,
    GraphPath,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
    RetrievedVerse,
)
from corpus.schemas import Corpus, ScriptureSource, Verse

logger = logging.getLogger(__name__)

# Channel weights for the fused score. Vector dominates, lexical breaks ties,
# graph evidence is a bonus rather than a primary signal (it is high precision
# but low coverage on a corpus this size).
VECTOR_WEIGHT = 0.55
FULLTEXT_WEIGHT = 0.20

#: ``intent -> scripture -> affinity in [0, 1]``, scaled by
#: ``settings.scripture_intent_boost`` in :func:`rerank`.
INTENT_SCRIPTURE_AFFINITY: dict[IntentLabel, dict[ScriptureSource, float]] = {
    IntentLabel.PROFESSIONAL_DILEMMA: {
        ScriptureSource.ARTHASHASTRA: 1.0,
        ScriptureSource.BHAGAVAD_GITA: 0.35,
    },
    IntentLabel.EMOTIONAL_SUPPORT: {
        ScriptureSource.BHAGAVAD_GITA: 1.0,
        ScriptureSource.ARTHASHASTRA: 0.05,
    },
    IntentLabel.SEEK_GUIDANCE: {
        ScriptureSource.BHAGAVAD_GITA: 0.8,
        ScriptureSource.ARTHASHASTRA: 0.45,
    },
    IntentLabel.SCRIPTURE_QUESTION: {
        ScriptureSource.BHAGAVAD_GITA: 0.6,
        ScriptureSource.ARTHASHASTRA: 0.6,
    },
    IntentLabel.CRISIS: {
        ScriptureSource.BHAGAVAD_GITA: 0.5,
        ScriptureSource.ARTHASHASTRA: 0.0,
    },
    IntentLabel.OTHER: {
        ScriptureSource.BHAGAVAD_GITA: 0.4,
        ScriptureSource.ARTHASHASTRA: 0.3,
    },
}

#: Relationship names, kept in sync with :mod:`kg.cypher_queries`.
REL_ADDRESSES_EMOTION = "ADDRESSES_EMOTION"
REL_HAS_THEME = "HAS_THEME"
REL_RELATED_TO = "RELATED_TO"


# Scoring helpers (pure functions - unit tested in tests/test_retrieval.py)
def scripture_affinity(intent: IntentLabel, source: ScriptureSource) -> float:
    """Prior probability that ``source`` is the right text for ``intent``."""
    return INTENT_SCRIPTURE_AFFINITY.get(intent, {}).get(source, 0.3)


def rerank(
    candidates: Iterable[RetrievedVerse],
    intent: IntentLabel = IntentLabel.SEEK_GUIDANCE,
    settings: Settings | None = None,
    top_k: int | None = None,
) -> list[RetrievedVerse]:
    """Fuse channel scores with the intent-conditioned scripture prior.

    ``final = 0.55*vector + 0.20*fulltext + graph_path_boost*graph
              + scripture_intent_boost*affinity(intent, source)``

    Mutates and returns the same objects (they carry the trace shown in the UI).
    Ties break on verse id so ordering is deterministic for evaluation.
    """
    settings = settings or get_settings()
    scored: list[RetrievedVerse] = []
    for candidate in candidates:
        boost = settings.scripture_intent_boost * scripture_affinity(intent, candidate.source)
        candidate.intent_boost = round(boost, 4)
        candidate.final_score = round(
            VECTOR_WEIGHT * candidate.vector_score
            + FULLTEXT_WEIGHT * candidate.fulltext_score
            + settings.graph_path_boost * candidate.graph_score
            + boost,
            6,
        )
        scored.append(candidate)

    scored.sort(key=lambda item: (-item.final_score, item.verse_id))
    limit = top_k if top_k is not None else settings.retrieval_top_k
    return scored[:limit]


def expand_query(query: str, emotion: EmotionResult | None, intent: IntentResult | None) -> str:
    """Append affect and intent terms so the lexical/vector channels see them."""
    parts = [query.strip()]
    if emotion is not None and emotion.labels:
        parts.append("Felt emotions: " + ", ".join(emotion.top_emotions(3)))
    if intent is not None:
        parts.append(f"Intent: {intent.intent.value.replace('_', ' ')}")
    return " | ".join(parts)


# Interface
class HybridRetriever(ABC):
    """Retrieval interface used by the orchestrator.

    ``retrieve`` takes two queries, not one. ``query`` is what the person
    actually wrote and drives the **fulltext** leg; ``vector_query``, when
    given, is an English rewrite of it and drives the **vector** leg instead.

    They are split because the two legs fail in opposite ways on a Hinglish
    message. Fulltext wants the person's own words - it is a lexical match, and
    substituting a paraphrase throws away the exact tokens it exists to find.
    The vector leg wants English, because the corpus is English: measured on the
    encoder in production, the same question scores 0.30 in English and 0.00 in
    Hinglish, and neither NIM multilingual encoder improves on that
    (kg_measurements section 14b). ``None`` means "no rewrite", and the
    retrievers then take exactly the path they took before this existed.
    """

    backend: str = "abstract"

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        top_k: int | None = None,
        vector_query: str | None = None,
    ) -> RetrievalTrace: ...

    async def aclose(self) -> None:
        return None

    def warmup(self) -> None:  # pragma: no cover - overridden where meaningful
        return None


# Offline implementation
class InMemoryHybridRetriever(HybridRetriever):
    """Full hybrid retrieval over an in-process corpus and graph.

    Same three channels and the same re-ranker as the Neo4j implementation, so
    behaviour differences come from the index quality only - which is exactly
    what ``eval/retrieval_eval.py`` measures when you switch backends.
    """

    backend = "in-memory-hybrid"

    def __init__(
        self,
        corpus: Corpus | None = None,
        embedder: Embedder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.corpus = corpus if corpus is not None else _load_default_corpus(self.settings)
        self.embedder = embedder or build_embedder(self.settings)

        self._verses: dict[str, Verse] = {verse.id: verse for verse in self.corpus}
        self._vectors: dict[str, list[float]] = {}
        self._doc_tokens: dict[str, list[str]] = {}
        self._idf: dict[str, float] = {}
        self._emotion_index: dict[str, list[str]] = defaultdict(list)
        self._theme_index: dict[str, list[str]] = defaultdict(list)
        self._concept_index: dict[str, list[str]] = defaultdict(list)
        self._build_index()

    # ------------------------------------------------------------- indexing
    def _build_index(self) -> None:
        texts = [verse.embedding_text() for verse in self.corpus]
        vectors = self.embedder.embed(texts) if texts else []
        document_frequency: dict[str, int] = defaultdict(int)

        for verse, vector in zip(self.corpus, vectors, strict=True):
            self._vectors[verse.id] = vector
            tokens = tokenize(f"{verse.text_english} {verse.commentary} {verse.transliteration}")
            self._doc_tokens[verse.id] = tokens
            for token in set(tokens):
                document_frequency[token] += 1
            for emotion in verse.emotions_addressed:
                self._emotion_index[emotion].append(verse.id)
            for theme in verse.themes:
                self._theme_index[theme].append(verse.id)
            for concept in verse.concepts:
                self._concept_index[concept].append(verse.id)

        total_docs = max(1, len(self._doc_tokens))
        self._idf = {
            token: math.log(1.0 + total_docs / count)
            for token, count in document_frequency.items()
        }
        logger.info(
            "in-memory index built: %d verses, %d emotions, %d themes",
            len(self._verses),
            len(self._emotion_index),
            len(self._theme_index),
        )

    def warmup(self) -> None:
        self.embedder.warmup()

    # --------------------------------------------------------------- channels
    def _vector_scores(self, query: str) -> dict[str, float]:
        query_vector = self.embedder.embed_one(query)
        return {
            verse_id: round(cosine_similarity(query_vector, vector), 6)
            for verse_id, vector in self._vectors.items()
        }

    def _fulltext_scores(self, query: str) -> dict[str, float]:
        """idf-weighted overlap, normalised by the best-scoring document."""
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return {}
        raw: dict[str, float] = {}
        for verse_id, tokens in self._doc_tokens.items():
            token_set = set(tokens)
            overlap = query_tokens & token_set
            if not overlap:
                continue
            raw[verse_id] = sum(self._idf.get(token, 0.0) for token in overlap)
        if not raw:
            return {}
        best = max(raw.values()) or 1.0
        return {verse_id: round(score / best, 6) for verse_id, score in raw.items()}

    def _graph_expansion(
        self,
        emotion: EmotionResult | None,
        seed_ids: Sequence[str],
        hops: int,
    ) -> tuple[dict[str, float], dict[str, list[GraphPath]]]:
        """Emotion hop (1) plus theme/related hop (2). Returns scores + paths."""
        scores: dict[str, float] = defaultdict(float)
        paths: dict[str, list[GraphPath]] = defaultdict(list)

        # ---- hop 1: (:Emotion)<-[:ADDRESSES_EMOTION]-(:Verse)
        if emotion is not None:
            for label in emotion.labels:
                for verse_id in self._emotion_index.get(label.emotion, []):
                    scores[verse_id] += label.score
                    paths[verse_id].append(
                        GraphPath(
                            nodes=[label.emotion, verse_id],
                            relationships=[REL_ADDRESSES_EMOTION],
                            description=(
                                f"detected emotion '{label.emotion}' "
                                f"<-[:{REL_ADDRESSES_EMOTION}]- ({verse_id})"
                            ),
                            hops=1,
                        )
                    )

        # ---- hop 2: (:Verse)-[:HAS_THEME]->(:Theme)<-[:HAS_THEME]-(:Verse)
        if hops >= 2:
            for seed_id in seed_ids:
                seed = self._verses.get(seed_id)
                if seed is None:
                    continue
                for theme in seed.themes:
                    for verse_id in self._theme_index.get(theme, []):
                        if verse_id == seed_id:
                            continue
                        scores[verse_id] += 0.35
                        paths[verse_id].append(
                            GraphPath(
                                nodes=[seed_id, theme, verse_id],
                                relationships=[REL_HAS_THEME, REL_HAS_THEME],
                                description=(
                                    f"({seed_id}) -[:{REL_HAS_THEME}]-> "
                                    f"theme '{theme}' <-[:{REL_HAS_THEME}]- ({verse_id})"
                                ),
                                hops=2,
                            )
                        )
                for concept in seed.concepts:
                    for verse_id in self._concept_index.get(concept, []):
                        if verse_id == seed_id:
                            continue
                        scores[verse_id] += 0.25
                        paths[verse_id].append(
                            GraphPath(
                                nodes=[seed_id, verse_id],
                                relationships=[REL_RELATED_TO],
                                description=(
                                    f"({seed_id}) -[:{REL_RELATED_TO} "
                                    f"{{shared: '{concept}'}}]-> ({verse_id})"
                                ),
                                hops=1,
                            )
                        )

        # Normalise so the graph channel stays comparable to the others.
        if scores:
            best = max(scores.values()) or 1.0
            scores = {verse_id: round(value / best, 6) for verse_id, value in scores.items()}
        return dict(scores), {key: value[:3] for key, value in paths.items()}

    # -------------------------------------------------------------- retrieve
    async def retrieve(
        self,
        query: str,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        top_k: int | None = None,
        vector_query: str | None = None,
    ) -> RetrievalTrace:
        started = time.perf_counter()
        settings = self.settings
        expanded = expand_query(query, emotion, intent)
        # Expanded separately rather than reusing `expanded`: the emotion and
        # intent terms are added per query, and the rewrite is a different
        # query.
        # Vector leg must be clean (unpolluted by appended emotion/intent text)
        # to avoid semantic drift and low similarity against verses.
        clean_vector = vector_query.strip() if vector_query else query.strip()
        expanded_vector = clean_vector

        vector_scores = self._vector_scores(expanded_vector)
        fulltext_scores = self._fulltext_scores(expanded)

        seeds = sorted(vector_scores, key=lambda vid: -vector_scores[vid])[:3]
        graph_scores, graph_paths = self._graph_expansion(
            emotion, seeds, settings.graph_expansion_hops
        )

        candidate_ids = _select_candidates(
            vector_scores, fulltext_scores, graph_scores, settings.retrieval_candidate_k
        )

        candidates: list[RetrievedVerse] = []
        for verse_id in candidate_ids:
            verse = self._verses.get(verse_id)
            if verse is None:
                continue
            channels: list[str] = []
            if vector_scores.get(verse_id, 0.0) > 0:
                channels.append("vector")
            if fulltext_scores.get(verse_id, 0.0) > 0:
                channels.append("fulltext")
            if graph_scores.get(verse_id, 0.0) > 0:
                channels.append("graph")
            candidates.append(
                RetrievedVerse(
                    verse=verse,
                    vector_score=vector_scores.get(verse_id, 0.0),
                    fulltext_score=fulltext_scores.get(verse_id, 0.0),
                    graph_score=graph_scores.get(verse_id, 0.0),
                    retrieval_channels=channels,
                    graph_paths=graph_paths.get(verse_id, []),
                )
            )

        intent_label = intent.intent if intent else IntentLabel.SEEK_GUIDANCE
        results = rerank(candidates, intent_label, settings, top_k)

        return RetrievalTrace(
            query=query,
            expanded_query=expanded,
            vector_query=vector_query or "",
            expanded_vector_query=expanded_vector if vector_query else "",
            emotions_used=emotion.top_emotions(3) if emotion else [],
            themes_used=sorted({theme for item in results for theme in item.verse.themes})[:8],
            candidates_considered=len(candidates),
            results=results,
            backend=self.backend,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def _select_candidates(
    vector_scores: Mapping[str, float],
    fulltext_scores: Mapping[str, float],
    graph_scores: Mapping[str, float],
    candidate_k: int,
) -> list[str]:
    """Union of the top-k of each channel (recall-oriented first stage)."""
    selected: dict[str, None] = {}
    for channel in (vector_scores, fulltext_scores, graph_scores):
        ranked = sorted(channel.items(), key=lambda kv: (-kv[1], kv[0]))[:candidate_k]
        for verse_id, score in ranked:
            if score > 0:
                selected.setdefault(verse_id, None)
    return list(selected)


def _load_default_corpus(settings: Settings) -> Corpus:
    """Load the corpus for the in-memory retriever.

    ``settings.corpus_path`` wins when set (tests and ablations pin it); the
    default order prefers a built corpus and falls back to the bundled sample.
    """
    from app.config import CORPUS_DATA_DIR, SAMPLE_CORPUS_PATH

    if settings.corpus_path:
        pinned = Path(settings.corpus_path)
        if pinned.exists():
            corpus = Corpus.load(pinned)
            logger.info("loaded pinned corpus %s (%d verses)", pinned, len(corpus))
            return corpus
        logger.error(
            "GITAGUIDE_CORPUS_PATH=%s does not exist; falling back to the search order",
            pinned,
        )

    for candidate in (
        CORPUS_DATA_DIR / "corpus.json",
        CORPUS_DATA_DIR / "gita.json",
        SAMPLE_CORPUS_PATH,
    ):
        if candidate.exists():
            corpus = Corpus.load(candidate)
            if candidate.name != "corpus.json":
                logger.info("loaded corpus %s (%d verses)", candidate.name, len(corpus))
            else:
                merged = Corpus(verses=list(corpus))
                gita = CORPUS_DATA_DIR / "gita.json"
                arthashastra = CORPUS_DATA_DIR / "arthashastra.json"
                for extra in (gita, arthashastra):
                    if extra.exists():
                        merged.extend(Corpus.load(extra))
                corpus = merged
            return corpus
    logger.warning("no corpus file found; retrieval will return nothing")
    return Corpus()


# Neo4j implementation
class RetrievalError(RuntimeError):
    """Retrieval failed in a way that must not be mistaken for "no results".

    Raised instead of returning an empty trace when the search call errors, or
    when the database returned records that could not be parsed at all. An empty
    result set is a legitimate answer; a broken query or a formatter mismatch is
    not, and the two must never look the same from the outside.
    """


#: ``$emotions`` is required by :data:`kg.cypher_queries.HYBRID_RETRIEVAL_QUERY`
#: and Cypher cannot default a missing parameter, so every call site starts from
#: this dict. Omitting it produces ``ParameterMissing`` at the driver, which is
#: exactly the failure this constant exists to prevent.
DEFAULT_QUERY_PARAMS: dict[str, Any] = {"emotions": [], "themes": []}

#: Columns the retrieval query returns; used by the result formatter and to
#: sanity-check that the query and the parser still agree.
VERSE_RECORD_KEYS: tuple[str, ...] = (
    "id",
    "source",
    "chapter",
    "verse",
    "book",
    "text_english",
    "text_sanskrit",
    "transliteration",
    "commentary",
    "citation",
    "themes",
    "matched_emotions",
    "related_via_emotion",
    "search_score",
)


def verse_result_formatter(record: Any) -> Any:
    """Turn a ``neo4j.Record`` into a **structured** ``RetrieverResultItem``.

    Why this exists
    ---------------
    ``neo4j-graphrag``'s default formatter is
    ``RetrieverResultItem(content=str(record), metadata=record.get("metadata"))``.
    With no ``metadata`` column that yields ``content`` as a *repr string* and
    ``metadata=None`` - unparseable downstream, which is precisely how retrieval
    came to report ``candidates_considered=0`` while the database was returning
    good hits. Passing this formatter to the retriever makes ``item.content`` a
    dict of the query's columns.

    Imported lazily by the retriever, and defensive about the record type so it
    can be unit-tested with a plain mapping.
    """
    from neo4j_graphrag.types import RetrieverResultItem  # lazy: package optional

    if hasattr(record, "data"):
        payload = dict(record.data())
    elif isinstance(record, Mapping):
        payload = dict(record)
    else:  # pragma: no cover - neo4j always gives one of the above
        payload = {"raw": str(record)}

    # The query also returns a `metadata` map; flatten it in so a single dict
    # carries everything regardless of which column the server populated.
    nested = payload.pop("metadata", None)
    if isinstance(nested, Mapping):
        for key, value in nested.items():
            payload.setdefault(key, value)

    return RetrieverResultItem(content=payload, metadata={"id": payload.get("id")})


def _supports_query_params(search_callable: Any) -> bool:
    """Does this ``neo4j-graphrag`` build accept ``query_params`` on search?

    Verified against the installed package rather than assumed from a version
    string: 1.18 accepts it, older builds may not, and a silent
    ``TypeError``-turned-empty-result is the failure mode to avoid.
    """
    try:
        signature = inspect.signature(search_callable)
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        return False
    parameters = signature.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return True
    return "query_params" in parameters


class _Neo4jGraphRAGEmbedderAdapter:
    """Adapt :class:`app.embeddings.Embedder` to the neo4j-graphrag protocol."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed_one(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embedder.embed(list(texts))


class Neo4jHybridRetriever(HybridRetriever):
    """Production retriever: ``neo4j-graphrag`` HybridCypherRetriever.

    The vector index + full-text index fusion is done by the package; the graph
    expansion and the path payload come from
    :data:`kg.cypher_queries.HYBRID_RETRIEVAL_QUERY`. Results are converted into
    the same :class:`RetrievedVerse` objects and pushed through the same
    :func:`rerank`, so the explanation panel and the evaluation harness do not
    care which backend produced them.
    """

    backend = "neo4j-hybrid-cypher"

    def __init__(
        self,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        driver: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or build_embedder(self.settings)
        self._driver = driver
        self._retriever: Any | None = None

    # ------------------------------------------------------------- plumbing
    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password),
            )
        return self._driver

    def _get_retriever(self) -> Any:
        if self._retriever is None:
            from neo4j_graphrag.retrievers import HybridCypherRetriever

            from kg.cypher_queries import HYBRID_RETRIEVAL_QUERY

            self._retriever = HybridCypherRetriever(
                driver=self._get_driver(),
                vector_index_name=self.settings.neo4j_vector_index,
                fulltext_index_name=self.settings.neo4j_fulltext_index,
                retrieval_query=HYBRID_RETRIEVAL_QUERY,
                embedder=_Neo4jGraphRAGEmbedderAdapter(self.embedder),
                #: Without this, item.content is a repr string - see
                #: verse_result_formatter for the failure it caused.
                result_formatter=verse_result_formatter,
                neo4j_database=self.settings.neo4j_database,
            )
        return self._retriever

    def warmup(self) -> None:
        self.embedder.warmup()
        self._get_driver().verify_connectivity()

    async def aclose(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            self._retriever = None

    # -------------------------------------------------------------- retrieve
    async def retrieve(
        self,
        query: str,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        top_k: int | None = None,
        vector_query: str | None = None,
    ) -> RetrievalTrace:
        started = time.perf_counter()
        settings = self.settings
        expanded = expand_query(query, emotion, intent)
        # Vector leg must be clean (unpolluted by appended emotion/intent text)
        # to avoid semantic drift and low similarity against verses.
        clean_vector = vector_query.strip() if vector_query else query.strip()
        expanded_vector = clean_vector
        emotions = emotion.top_emotions(settings.emotion_top_k) if emotion else []

        # Always a complete parameter set: the retrieval query requires
        # $emotions and $themes (Cypher cannot default them).
        from corpus.tagging import tag_themes
        matched_query_themes = tag_themes(query) or []
        query_params = {**DEFAULT_QUERY_PARAMS, "emotions": emotions, "themes": matched_query_themes}
        candidate_k = settings.retrieval_candidate_k

        items = self._search(
            expanded,
            candidate_k,
            query_params,
            vector_text=expanded_vector if vector_query else None,
        )

        candidates: list[RetrievedVerse] = []
        parse_failures = 0
        for item in items:
            record = _record_to_dict(item)
            if not record.get("id"):
                parse_failures += 1
                continue
            try:
                candidates.append(_record_to_retrieved_verse(record, emotions))
            except (ValueError, KeyError, TypeError):
                parse_failures += 1
                logger.exception("could not build a Verse from record %s", _shape_of(record))

        if items and not candidates:
            # The loud version of the bug this method used to have: records came
            # back and every single one was discarded. Never report that as "no
            # results" - it is a query/parser contract break.
            logger.error(
                "RETRIEVAL PARSE FAILURE: %d record(s) returned, 0 parsed. "
                "Expected keys %s; first item was type=%s shape=%s. "
                "Is result_formatter=verse_result_formatter still wired, and does "
                "HYBRID_RETRIEVAL_QUERY still return the `id` column?",
                len(items),
                [*VERSE_RECORD_KEYS[:6], "..."],
                type(items[0]).__name__,
                _shape_of(items[0]),
            )
            raise RetrievalError(
                f"neo4j returned {len(items)} record(s) but none could be parsed "
                "(see the RETRIEVAL PARSE FAILURE log for the record shape)"
            )
        if parse_failures:
            logger.warning(
                "retrieval dropped %d/%d unparseable record(s)", parse_failures, len(items)
            )

        intent_label = intent.intent if intent else IntentLabel.SEEK_GUIDANCE
        results = rerank(candidates, intent_label, settings, top_k)

        if not items:
            logger.info(
                "retrieval returned 0 records for %r (emotions=%s) - "
                "empty index, or the query genuinely matches nothing",
                expanded[:120],
                emotions,
            )

        return RetrievalTrace(
            query=query,
            expanded_query=expanded,
            vector_query=vector_query or "",
            expanded_vector_query=expanded_vector if vector_query else "",
            emotions_used=emotions,
            themes_used=sorted({theme for item in results for theme in item.verse.themes})[:8],
            candidates_considered=len(candidates),
            parse_failures=parse_failures,
            results=results,
            backend=self.backend,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    # ---------------------------------------------------------------- search
    def _search(
        self,
        query_text: str,
        top_k: int,
        query_params: dict[str, Any],
        vector_text: str | None = None,
    ) -> list[Any]:
        """Run the hybrid search, loudly.

        Prefers the library's parameterised ``search()``. If the installed build
        cannot pass parameters into the retrieval query, falls back to a
        driver-level execution of the same Cypher (see
        :data:`kg.cypher_queries.MANUAL_HYBRID_QUERY`) rather than dropping the
        ``$emotions`` parameter and letting the query fail.

        ``vector_text``, when given, is embedded and passed as ``query_vector``
        so the vector leg searches for the rewrite while the fulltext leg keeps
        ``query_text``. It is passed **only** when it differs: with no rewrite
        the library embeds ``query_text`` itself, exactly as before, and this
        method's behaviour is unchanged down to the argument list.
        """
        retriever = self._get_retriever()

        if not _supports_query_params(retriever.search):
            logger.warning(
                "installed neo4j-graphrag cannot parameterise the retrieval query; "
                "using the driver-level hybrid query instead"
            )
            return self._manual_search(query_text, top_k, query_params, vector_text)

        extra: dict[str, Any] = {}
        if vector_text:
            extra["query_vector"] = self.embedder.embed_one(vector_text)

        try:
            raw = retriever.search(
                query_text=query_text,
                top_k=top_k,
                query_params=query_params,
                **extra,
            )
        except Exception as exc:
            logger.error(
                "RETRIEVAL SEARCH FAILED on %s (vector_index=%s fulltext_index=%s "
                "db=%s top_k=%d params=%s): %s",
                self.settings.neo4j_uri,
                self.settings.neo4j_vector_index,
                self.settings.neo4j_fulltext_index,
                self.settings.neo4j_database,
                top_k,
                sorted(query_params),
                exc,
                exc_info=True,
            )
            raise RetrievalError(f"hybrid search failed: {exc}") from exc

        return list(getattr(raw, "items", None) or [])

    def _manual_search(
        self,
        query_text: str,
        top_k: int,
        query_params: dict[str, Any],
        vector_text: str | None = None,
    ) -> list[Any]:
        """Driver-level hybrid search + retrieval tail, fully parameterised."""
        from kg.cypher_queries import (  # lazy: keeps import cheap
            HYBRID_RETRIEVAL_QUERY,
            MANUAL_HYBRID_QUERY,
        )

        cypher = f"{MANUAL_HYBRID_QUERY}\n{HYBRID_RETRIEVAL_QUERY}"
        parameters = {
            **query_params,
            "vector_index_name": self.settings.neo4j_vector_index,
            "fulltext_index_name": self.settings.neo4j_fulltext_index,
            "query_text": query_text,
            "query_vector": self.embedder.embed_one(vector_text or query_text),
            "top_k": top_k,
        }
        try:
            records, _, _ = self._get_driver().execute_query(
                cypher,
                parameters,
                database_=self.settings.neo4j_database,
            )
        except Exception as exc:
            logger.error(
                "RETRIEVAL SEARCH FAILED (driver-level path, params=%s): %s",
                sorted(parameters),
                exc,
                exc_info=True,
            )
            raise RetrievalError(f"driver-level hybrid search failed: {exc}") from exc

        return [verse_result_formatter(record) for record in records]


def _shape_of(item: Any) -> str:
    """Short, log-safe description of an unparsed item (keys or a snippet)."""
    content = getattr(item, "content", item)
    if isinstance(content, Mapping):
        return f"mapping(keys={sorted(content)[:8]})"
    text = str(content)
    return f"{type(content).__name__}({text[:160]!r})"


def _record_to_dict(item: Any) -> dict[str, Any]:
    """``RetrieverResultItem`` (or ``neo4j.Record``) -> plain dict.

    Four shapes are accepted, in order of preference:

    1. ``content`` is already a mapping - what
       :func:`verse_result_formatter` produces (the normal path);
    2. the item is a ``neo4j.Record`` (``.data()``);
    3. ``metadata`` carries the payload;
    4. ``content`` is a **repr string** - the default-formatter shape. Recovered
       with ``ast.literal_eval`` on a best-effort basis and logged as a warning,
       because it means the formatter is not wired and precision is being lost.

    Returns ``{}`` only when nothing usable is present; the caller counts those
    and escalates.
    """
    content = getattr(item, "content", item)

    if isinstance(content, Mapping):
        return dict(content)

    data = getattr(item, "data", None)
    if callable(data):
        try:
            record = data()
        except Exception:  # pragma: no cover - defensive
            record = None
        if isinstance(record, Mapping):
            return dict(record)

    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, Mapping) and metadata.get("id"):
        return dict(metadata)

    if isinstance(content, str) and content:
        recovered = _parse_record_repr(content)
        if recovered:
            logger.warning(
                "recovered a retrieval record from its repr string - the "
                "result_formatter is not wired (expected verse_result_formatter)"
            )
            return recovered

    return {}


#: ``<Record id='BG-2.47' chapter=2 ...>`` -> the ``key=value`` pairs inside.
_RECORD_REPR_RE = re.compile(r"^<Record\s+(?P<body>.*)>$", re.DOTALL)
_RECORD_FIELD_RE = re.compile(r"(\w+)=")


def _parse_record_repr(text: str) -> dict[str, Any]:
    """Best-effort recovery of a ``neo4j.Record`` repr into a dict.

    Only a fallback: :func:`verse_result_formatter` exists so this never runs on
    the normal path. Values are parsed with :func:`ast.literal_eval`, so node
    objects and other non-literals are skipped rather than guessed at.
    """
    match = _RECORD_REPR_RE.match(text.strip())
    if not match:
        return {}

    body = match.group("body")
    starts = [(m.start(), m.group(1)) for m in _RECORD_FIELD_RE.finditer(body)]
    out: dict[str, Any] = {}
    for index, (position, key) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(body)
        raw = body[position + len(key) + 1 : end].strip().rstrip(",").strip()
        try:
            out[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            continue
    return out if out.get("id") else {}


def _record_to_retrieved_verse(record: Mapping[str, Any], emotions: Sequence[str]) -> RetrievedVerse:
    """Rebuild a :class:`RetrievedVerse` (and its paths) from a Cypher record."""
    source = ScriptureSource(record.get("source", ScriptureSource.BHAGAVAD_GITA.value))
    verse = Verse(
        id=str(record["id"]),
        source=source,
        chapter=int(record.get("chapter") or 0),
        verse=int(record.get("verse") or 0),
        book=int(record["book"]) if record.get("book") is not None else None,
        text_english=str(record.get("text_english") or ""),
        text_sanskrit=str(record.get("text_sanskrit") or ""),
        transliteration=str(record.get("transliteration") or ""),
        commentary=str(record.get("commentary") or ""),
        themes=list(record.get("themes") or []),
        emotions_addressed=list(record.get("matched_emotions") or []),
    )

    paths: list[GraphPath] = []
    for matched in record.get("matched_emotions") or []:
        paths.append(
            GraphPath(
                nodes=[str(matched), verse.id],
                relationships=[REL_ADDRESSES_EMOTION],
                description=(
                    f"detected emotion '{matched}' "
                    f"<-[:{REL_ADDRESSES_EMOTION}]- ({verse.id})"
                ),
                hops=1,
            )
        )
    for theme_match in record.get("matched_themes") or []:
        paths.append(
            GraphPath(
                nodes=[verse.id, str(theme_match)],
                relationships=[REL_HAS_THEME],
                description=(
                    f"({verse.id}) -[:{REL_HAS_THEME}]-> "
                    f"theme '{theme_match}'"
                ),
                hops=1,
            )
        )
    for sibling in record.get("related_via_emotion") or []:
        if not isinstance(sibling, Mapping):
            continue
        paths.append(
            GraphPath(
                nodes=[verse.id, str(sibling.get("id"))],
                relationships=[REL_RELATED_TO, REL_ADDRESSES_EMOTION],
                description=(
                    f"({verse.id}) -[:{REL_RELATED_TO}]- ({sibling.get('id')}) "
                    f"-[:{REL_ADDRESSES_EMOTION}]-> '{sibling.get('via')}'"
                ),
                hops=2,
            )
        )

    search_score = float(record.get("search_score") or 0.0)
    graph_score = min(1.0, 0.5 * len(paths)) if paths else 0.0
    channels = ["vector", "fulltext"] + (["graph"] if paths else [])
    return RetrievedVerse(
        verse=verse,
        vector_score=min(1.0, search_score),
        fulltext_score=min(1.0, search_score),
        graph_score=graph_score,
        retrieval_channels=channels,
        graph_paths=paths[:3],
    )


# Factory
def build_retriever(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    corpus: Corpus | None = None,
    strict: bool = False,
) -> HybridRetriever:
    """Neo4j retriever when configured, otherwise the in-memory one.

    ``strict=True`` refuses the fallback and re-raises. Measurement callers must
    use it: the two backends have different retrieval semantics (Neo4j annotates
    the vector/full-text hits, the in-memory retriever expands the candidate set
    through the graph), so silently swapping one for the other changes *what was
    measured* without changing the number's appearance. A crashed eval is
    recoverable; a number attributed to the wrong system is not.

    The application path keeps ``strict=False`` on purpose - serving should
    degrade rather than go down.
    """
    settings = settings or get_settings()
    if settings.neo4j_enabled:
        try:
            retriever = Neo4jHybridRetriever(settings, embedder)
            retriever.warmup()
            logger.info("using Neo4j hybrid retriever at %s", settings.neo4j_uri)
            return retriever
        except Exception as exc:  # pragma: no cover - depends on a live database
            if strict:
                logger.error(
                    "RETRIEVER UNAVAILABLE (strict): Neo4j at %s could not be reached, "
                    "and falling back to the in-memory retriever would silently change "
                    "which system is being measured: %s",
                    settings.neo4j_uri,
                    exc,
                    exc_info=True,
                )
                raise RetrievalError(
                    f"strict build_retriever: Neo4j at {settings.neo4j_uri} is "
                    f"unavailable ({exc}). Refusing to fall back to the in-memory "
                    "retriever - it has different retrieval semantics, so the "
                    "resulting metrics would not be production numbers."
                ) from exc
            logger.exception("Neo4j unavailable; falling back to in-memory retriever")
    return InMemoryHybridRetriever(corpus=corpus, embedder=embedder, settings=settings)
