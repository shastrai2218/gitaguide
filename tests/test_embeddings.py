"""Embedder tests, including the ONNX/torch parity check.

The parity test is the important one. The Neo4j vector index was built by the
``sentence-transformers`` encoder; the serving path now uses an ONNX export run
on onnxruntime. If the two ever disagree, every query vector becomes
incommensurate with the stored ones and retrieval degrades toward noise while
every health check still reports green. That failure has no other alarm, so it
gets a test.

It is **opt-in**, because the project's rule 6 is that ``pytest`` runs on a
machine with no model cache and no weights download. Run it deliberately, after
any change to the embedder or its settings::

    GITAGUIDE_RUN_PARITY=1 pytest tests/test_embeddings.py -m slow

It needs ``requirements-dev.txt`` installed (it is the torch encoder that is
being compared against) and takes roughly a minute.
"""

from __future__ import annotations

import math
import os

import pytest

from app.config import Settings
from app.embeddings import HashingEmbedder, cosine_similarity, tokenize

#: Deliberately mixed: workplace English, grief, a scripture question, Hinglish,
#: corpus text, and two degenerate inputs.
PARITY_TEXTS = [
    "My manager overrules every decision I make",
    "I am grieving my father",
    "What does the Gita say about detachment from results?",
    "boss ne mera credit le liya, ab kya karun",
    "When an inferior king is all submissive, peace should be made with him.",
    "a",
    "   ",
]


# --------------------------------------------------------------------------- #
# Offline embedder
# --------------------------------------------------------------------------- #
def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(dim=384)
    first = embedder.embed_one("my manager took credit for my work")
    second = embedder.embed_one("my manager took credit for my work")
    assert first == second
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-9)


def test_tokenize_drops_stopwords() -> None:
    assert tokenize("The manager and I are in the room") == ["manager", "room"]


def test_cosine_similarity_is_clamped() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert math.isclose(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0, rel_tol=1e-9)


# --------------------------------------------------------------------------- #
# Parity with the encoder that built the index
# --------------------------------------------------------------------------- #
@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("GITAGUIDE_RUN_PARITY") != "1",
    reason="downloads weights; set GITAGUIDE_RUN_PARITY=1 to run",
)
def test_onnx_matches_the_torch_encoder() -> None:
    """The ONNX embedder must reproduce the vectors already in the index.

    Tolerance is float32 noise, not "close enough": these vectors are compared
    by cosine against stored ones, so anything above ~1e-5 of drift would be a
    real change in retrieval behaviour.
    """
    pytest.importorskip(
        "sentence_transformers",
        reason="torch reference encoder is a dev-only dependency",
    )
    from app.embeddings import OnnxMiniLMEmbedder
    from eval.torch_reference import SentenceTransformerEmbedder

    settings = Settings(mock_mode=False)
    onnx_vectors = OnnxMiniLMEmbedder(settings).embed(PARITY_TEXTS)
    torch_vectors = SentenceTransformerEmbedder(settings).embed(PARITY_TEXTS)

    assert len(onnx_vectors) == len(torch_vectors) == len(PARITY_TEXTS)
    for text, left, right in zip(PARITY_TEXTS, onnx_vectors, torch_vectors, strict=True):
        assert len(left) == len(right) == settings.embedding_dim
        similarity = sum(a * b for a, b in zip(left, right, strict=True))
        assert similarity > 0.99999, f"ONNX drifted from the torch encoder on {text!r}"
        assert math.isclose(
            math.sqrt(sum(a * a for a in left)), 1.0, rel_tol=1e-5
        ), "vectors must be L2-normalised, as the Pooling+Normalize modules do"
