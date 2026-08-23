"""Stage 4 - hybrid retrieval (vector + full-text + graph expansion).

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

import logging
import math
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
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


# --------------------------------------------------------------------------- #
# Scoring helpers (pure functions - unit tested in tests/test_retrieval.py)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class HybridRetriever(ABC):
    """Retrieval interface used by the orchestrator."""

    backend: str = "abstract"

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        top_k: int | None = None,
    ) -> RetrievalTrace: ...

    async def aclose(self) -> None:
        return None

    def warmup(self) -> None:  # pragma: no cover - overridden where meaningful
        return None


# --------------------------------------------------------------------------- #
# Offline implementation
# --------------------------------------------------------------------------- #
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
    ) -> RetrievalTrace:
        started = time.perf_counter()
        settings = self.settings
        expanded = expand_query(query, emotion, intent)

        vector_scores = self._vector_scores(expanded)
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
    """Load the bundled sample corpus (offline default)."""
    from app.config import CORPUS_DATA_DIR, SAMPLE_CORPUS_PATH

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


# --------------------------------------------------------------------------- #
# Neo4j implementation
# --------------------------------------------------------------------------- #
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
    ) -> RetrievalTrace:
        started = time.perf_counter()
        settings = self.settings
        expanded = expand_query(query, emotion, intent)
        emotions = emotion.top_emotions(settings.emotion_top_k) if emotion else []

        retriever = self._get_retriever()
        raw = retriever.search(
            query_text=expanded,
            top_k=settings.retrieval_candidate_k,
            query_params={"emotions": emotions},
        )

        candidates: list[RetrievedVerse] = []
        for item in getattr(raw, "items", []) or []:
            record = _record_to_dict(item)
            if not record.get("id"):
                continue
            candidates.append(_record_to_retrieved_verse(record, emotions))

        intent_label = intent.intent if intent else IntentLabel.SEEK_GUIDANCE
        results = rerank(candidates, intent_label, settings, top_k)

        return RetrievalTrace(
            query=query,
            expanded_query=expanded,
            emotions_used=emotions,
            themes_used=sorted({theme for item in results for theme in item.verse.themes})[:8],
            candidates_considered=len(candidates),
            results=results,
            backend=self.backend,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def _record_to_dict(item: Any) -> dict[str, Any]:
    """``RetrieverResultItem`` -> plain dict (content may be a repr string)."""
    content = getattr(item, "content", item)
    if isinstance(content, Mapping):
        return dict(content)
    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, Mapping) and metadata.get("id"):
        return dict(metadata)
    return {}


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


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_retriever(
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    corpus: Corpus | None = None,
) -> HybridRetriever:
    """Neo4j retriever when configured, otherwise the in-memory one."""
    settings = settings or get_settings()
    if settings.neo4j_enabled:
        try:
            retriever = Neo4jHybridRetriever(settings, embedder)
            retriever.warmup()
            logger.info("using Neo4j hybrid retriever at %s", settings.neo4j_uri)
            return retriever
        except Exception:  # pragma: no cover - depends on a live database
            logger.exception("Neo4j unavailable; falling back to in-memory retriever")
    return InMemoryHybridRetriever(corpus=corpus, embedder=embedder, settings=settings)
