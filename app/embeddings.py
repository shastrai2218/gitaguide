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


class EmbeddingError(RuntimeError):
    """The real embedder is unusable and a fallback would be dishonest."""

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


class OnnxMiniLMEmbedder(Embedder):
    """``all-MiniLM-L6-v2`` on onnxruntime, with no torch in the process.

    The instance sets :attr:`name` in ``__init__`` because it carries the
    precision, which callers use to tell one encoder's vectors from another's.

    Why this exists
    ---------------
    ``sentence-transformers`` was the only torch dependency on the serving path,
    and torch is ~4.4 GB installed. The model itself is a 6-layer MiniLM; it
    does not need a training framework to run a forward pass. This backend runs
    the repository's own fp32 ONNX export on onnxruntime and reproduces
    sentence-transformers' pipeline explicitly: mean-pool the token embeddings
    under the attention mask, then L2-normalise.

    Precision
    ---------
    ``fp32`` by default; ``int8`` when ``embedder_precision`` says so. The INT8
    file is the *same MiniLM*, quantised - not a different model.

    **Whichever is chosen must also have written the index.** Vectors are
    compared by cosine, so an INT8 encoder against an fp32-built index drifts to
    ~0.986 and quietly returns worse neighbours; nothing errors. Used on both
    sides it is self-consistent and that penalty does not exist. The precision
    is therefore part of :attr:`name`, so ``/health`` and every eval artefact
    record which encoder produced the numbers.

    Parity of the fp32 export against the torch encoder is pinned by
    ``tests/test_embeddings.py``. Heavy imports are inside
    :meth:`_ensure_loaded`, never at module import.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.dim = self.settings.embedding_dim
        self.precision = self.settings.embedder_precision
        self.name = f"all-MiniLM-L6-v2-onnx-{self.precision}"
        self._session: object | None = None
        self._tokenizer: object | None = None

    @property
    def onnx_file(self) -> str:
        """The export this instance loads, chosen by configured precision."""
        if self.precision == "int8":
            return self.settings.embedding_onnx_file_int8
        return self.settings.embedding_onnx_file

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import AutoTokenizer

        settings = self.settings
        logger.info(
            "loading embedding model %s (%s, onnx %s)",
            settings.embedding_model_id,
            self.onnx_file,
            self.precision,
        )
        model_path = hf_hub_download(
            repo_id=settings.embedding_model_id,
            filename=self.onnx_file,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.onnx_intra_op_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = AutoTokenizer.from_pretrained(settings.embedding_model_id)

        shape = self._session.get_outputs()[0].shape  # type: ignore[attr-defined]
        hidden = shape[-1] if isinstance(shape[-1], int) else settings.embedding_dim
        self.dim = int(hidden)

    def warmup(self) -> None:
        self._ensure_loaded()
        self.embed(["warmup"])

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._ensure_loaded()
        assert self._session is not None and self._tokenizer is not None
        import numpy as np

        encoded = self._tokenizer(  # type: ignore[operator]
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.settings.embedding_max_seq_length,
            return_tensors="np",
        )
        input_names = {item.name for item in self._session.get_inputs()}  # type: ignore[attr-defined]
        feed = {name: value for name, value in encoded.items() if name in input_names}
        hidden_states = self._session.run(None, feed)[0]  # type: ignore[attr-defined]

        # Mean pooling under the attention mask, exactly as the Pooling module
        # in the sentence-transformers config does it.
        mask = encoded["attention_mask"].astype(hidden_states.dtype)[..., None]
        summed = (hidden_states * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
        pooled = summed / counts

        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), a_min=1e-12, a_max=None)
        return [[float(value) for value in row] for row in pooled / norms]


def build_embedder(settings: Settings | None = None) -> Embedder:
    """Real embedder when online, hashing embedder in mock mode."""
    settings = settings or get_settings()
    if settings.mock_mode:
        return HashingEmbedder(dim=settings.embedding_dim)

    embedder = OnnxMiniLMEmbedder(settings)
    try:
        embedder.warmup()
    except Exception as exc:  # pragma: no cover - depends on the model cache
        # Never degrade silently. The hashing embedder produces vectors that
        # are unrelated to the MiniLM vectors in the Neo4j index, so falling
        # back to it would leave retrieval returning near-random verses while
        # every component still reported healthy.
        raise EmbeddingError(
            f"the real embedder could not be loaded ({exc}). Retrieval would "
            "return meaningless results against a MiniLM-built index, so this "
            "is fatal. Set GITAGUIDE_MOCK_MODE=true to run offline."
        ) from exc
    return embedder
