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


# Offline embedder
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


# Parity with the encoder that built the index
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


# Precision selection
def test_fp32_is_the_default_so_local_development_is_unchanged() -> None:
    """Switching precision must be a deliberate act, not a surprise."""
    from app.embeddings import OnnxMiniLMEmbedder

    settings = Settings(mock_mode=False)
    assert settings.embedder_precision == "fp32"
    assert OnnxMiniLMEmbedder(settings).onnx_file == settings.embedding_onnx_file


def test_int8_selects_the_quantised_export_of_the_same_model() -> None:
    """Same MiniLM, quantised - not a different model."""
    from app.embeddings import OnnxMiniLMEmbedder

    settings = Settings(mock_mode=False, embedder_precision="int8")
    embedder = OnnxMiniLMEmbedder(settings)

    assert embedder.onnx_file == settings.embedding_onnx_file_int8
    assert embedder.onnx_file != settings.embedding_onnx_file
    # The repository is the model identity; quantisation must not change it.
    assert settings.embedding_model_id == Settings(mock_mode=False).embedding_model_id


def test_the_encoder_name_carries_its_precision() -> None:
    """An index and a query encoder must be comparable by name.

    The failure this guards against is silent: an INT8 encoder querying an
    fp32-built index returns worse neighbours and reports nothing. Putting the
    precision in the name means ``/health``, the eval provenance and the graph
    manifest all say which encoder produced their vectors.
    """
    from app.embeddings import OnnxMiniLMEmbedder

    fp32 = OnnxMiniLMEmbedder(Settings(mock_mode=False, embedder_precision="fp32"))
    int8 = OnnxMiniLMEmbedder(Settings(mock_mode=False, embedder_precision="int8"))

    assert fp32.name.endswith("-fp32")
    assert int8.name.endswith("-int8")
    assert fp32.name != int8.name


def test_an_unknown_precision_is_rejected_at_config_time() -> None:
    """A typo must fail at startup, not silently fall back to fp32."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        Settings(mock_mode=False, embedder_precision="int4")


def test_embedding_is_batched_so_peak_memory_is_bounded() -> None:
    """Peak allocation must be a property of the embedder, not of the caller.

    The hidden-state tensor is ``batch x tokens x 384`` float32. Unbatched, a
    call scales with the caller's list: embedding the 3,836-verse corpus in one
    pass asked numpy for 1.4 GiB and raised. On the 512 MB instance a far
    smaller list would be fatal, so the bound belongs here.
    """
    from app.embeddings import OnnxMiniLMEmbedder

    settings = Settings(mock_mode=False, embedder_precision="fp32", embedding_batch_size=8)
    embedder = OnnxMiniLMEmbedder(settings)

    calls: list[int] = []
    original = embedder._embed_batch

    def counting(texts):
        calls.append(len(texts))
        return original(texts)

    embedder._embed_batch = counting  # type: ignore[method-assign]
    vectors = embedder.embed([f"verse number {index} about duty" for index in range(20)])

    assert len(vectors) == 20, "every input must still get a vector, in order"
    assert calls == [8, 8, 4], f"expected three bounded batches, got {calls}"


def test_int8_always_embeds_one_text_at_a_time() -> None:
    """INT8 is not batch-invariant, so it must never batch.

    Measured 7 Sep 2026: the fp32 export gives cosine 1.000000 whether texts are
    embedded singly or 64 at a time; the INT8 export gives 0.980, because a
    padded batch and an unpadded single text quantise differently. Serving
    embeds one query per call, so a batched bulk re-embed would have put the
    index and the query encoder ~0.98 apart - exactly the mismatch that choosing
    INT8 on both sides was meant to remove.
    """
    from app.embeddings import OnnxMiniLMEmbedder

    embedder = OnnxMiniLMEmbedder(
        Settings(mock_mode=False, embedder_precision="int8", embedding_batch_size=64)
    )
    assert embedder.effective_batch_size == 1, (
        "INT8 must embed one text at a time regardless of the configured batch "
        "size, or the index and the query encoder drift apart"
    )

    calls: list[int] = []
    original = embedder._embed_batch

    def counting(texts):
        calls.append(len(texts))
        return original(texts)

    embedder._embed_batch = counting  # type: ignore[method-assign]
    embedder.embed([f"text {index}" for index in range(5)])
    assert calls == [1, 1, 1, 1, 1]


def test_fp32_honours_the_configured_batch_size() -> None:
    """It is batch-invariant, so batching is a free memory/speed trade there."""
    from app.embeddings import OnnxMiniLMEmbedder

    embedder = OnnxMiniLMEmbedder(
        Settings(mock_mode=False, embedder_precision="fp32", embedding_batch_size=16)
    )
    assert embedder.effective_batch_size == 16


def test_batching_does_not_change_the_vectors() -> None:
    """Splitting the work must not change the answer, at either precision.

    This is the property the index depends on: whatever wrote the corpus and
    whatever embeds a query must agree, and they run with different list
    lengths.
    """
    from app.embeddings import OnnxMiniLMEmbedder

    texts = [f"a sentence about work and duty, number {index}" for index in range(5)]
    for precision in ("fp32", "int8"):
        singly = OnnxMiniLMEmbedder(
            Settings(
                mock_mode=False, embedder_precision=precision, embedding_batch_size=1
            )
        ).embed(texts)
        together = OnnxMiniLMEmbedder(
            Settings(
                mock_mode=False, embedder_precision=precision, embedding_batch_size=64
            )
        ).embed(texts)

        for left, right in zip(singly, together, strict=True):
            similarity = sum(a * b for a, b in zip(left, right, strict=True))
            assert similarity > 0.99999, (
                f"{precision}: batch size changed the embedding, so the index and "
                "the query encoder would disagree"
            )
