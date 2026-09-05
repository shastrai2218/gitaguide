"""The torch reference encoder — development and verification only.

Research-pipeline role
----------------------
The Neo4j vector index was built with ``sentence-transformers``. When the
serving path moved to onnxruntime (Day 3) the replacement had to be shown to
produce the *same* vectors, or every stored embedding would have silently
become incommensurate with every query embedding.

This module holds that reference implementation. It lives under ``eval/``, not
``app/``, for one reason: importing it pulls torch (~4.4 GB), and the serving
runtime must not be able to reach it even by accident. ``requirements.txt`` does
not install ``sentence-transformers``; ``requirements-dev.txt`` does.

Used by ``tests/test_embeddings.py::test_onnx_matches_the_torch_encoder``, which
skips when the package is absent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.embeddings import Embedder

logger = logging.getLogger(__name__)


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
