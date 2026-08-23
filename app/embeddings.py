"""Sentence embeddings for the vector half of hybrid retrieval.

Research-pipeline role
---------------------
The KG builder embeds every verse with ``all-MiniLM-L6-v2`` and writes the
vectors into the Neo4j vector index; the retriever embeds the user query with
the *same* embedder. To keep rule 1 (offline mode) true, a deterministic hashing
embedder implements the identical interface: it is not semantically good, but it
is stable, fast and dependency-free, so retrieval logic and the re-ranker can be
developed and unit-tested without downloading weights.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Very small English stop list - enough for the offline lexical channel.
STOPWORDS: frozenset[str] = frozenset(
    ["a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "of", "in", "on", "at", "to", "for", "from", "by", "with", "is", "am", "are", "was", "were", "be", "been", "being", "do", "does", "did", "doing", "have", "has", "had", "having", "i", "me", "my", "we", "our", "you", "your", "he", "him", "his", "she", "her", "they", "them", "it", "its", "as", "so", "not", "no", "there", "here", "what", "which", "who", "whom", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "only", "own", "same", "too", "very", "can", "will", "just", "should", "now", "about", "into", "over", "under", "again", "further", "once"]
)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens with stopwords removed."""
    return [token for token in _TOKEN_RE.findall(text.lower()) if token not in STOPWORDS]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity clamped to ``[0, 1]`` (vectors here are non-negative)."""
    if not left or not right:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


class Embedder(ABC):
    """Common embedding interface (batch in, list-of-vectors out)."""

    name: str = "abstract"
    dim: int = 0

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def warmup(self) -> None:  # pragma: no cover - trivial for the fake
        self.embed(["warmup"])


class HashingEmbedder(Embedder):
    """Deterministic offline embedder (hashed bag of words, L2-normalised).

    Token -> bucket via BLAKE2b, value weighted by ``1/sqrt(position)`` so word
    order has a mild effect. Similar sentences share buckets, which is enough
    for the re-ranker's unit tests to be meaningful.
    """

    name = "hashing-offline"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = tokenize(text)
        for position, token in enumerate(tokens, start=1):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign * (1.0 / math.sqrt(position))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class SentenceTransformerEmbedder(Embedder):
    """``sentence-transformers/all-MiniLM-L6-v2`` (lazy-loaded, CPU)."""

    name = "all-MiniLM-L6-v2"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.dim = self.settings.embedding_dim
        self._model: object | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("loading embedding model %s", self.settings.embedding_model_id)
        self._model = SentenceTransformer(self.settings.embedding_model_id, device="cpu")
        self.dim = int(self._model.get_sentence_embedding_dimension())  # type: ignore[attr-defined]

    def warmup(self) -> None:
        self._ensure_loaded()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_loaded()
        assert self._model is not None
        vectors = self._model.encode(  # type: ignore[attr-defined]
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [[float(value) for value in row] for row in vectors]


def build_embedder(settings: Settings | None = None) -> Embedder:
    """Real embedder when online, hashing embedder in mock mode."""
    settings = settings or get_settings()
    if settings.mock_mode:
        return HashingEmbedder(dim=settings.embedding_dim)
    try:
        embedder = SentenceTransformerEmbedder(settings)
        embedder.warmup()
        return embedder
    except Exception:  # pragma: no cover - depends on local model cache
        logger.exception("sentence-transformers unavailable; using hashing embedder")
        return HashingEmbedder(dim=settings.embedding_dim)
