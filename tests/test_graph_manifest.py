"""The export manifest says which encoder wrote the vectors, and the import believes it.

Offline: no Neo4j, no encoder. The round-trip test (``test_graph_roundtrip.py``)
proves the scripts against a real database; this module proves the one check
that database counts cannot make.

Why it exists
-------------
On 8 Sep 2026 ``data/graph_export`` held an fp32 export while the local graph
had been re-embedded to INT8 and production was configured for INT8. Every
count in the manifest matched. Only re-embedding a sample and comparing cosines
told the two apart. The exporter now does that measurement itself and records
the answer; the importer refuses a manifest whose answer disagrees with the
environment it is importing for.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings, reset_settings_cache
from scripts import export_neo4j, import_neo4j

RELATIONSHIPS = {
    "edges": {"ADDRESSES_EMOTION": [1], "HAS_THEME": [1, 2], "MENTIONS": [1],
              "PART_OF": [1, 2], "RELATED_TO": [1]},
    "label_nodes": {"Scripture": [1, 2]},
}


def write_manifest(directory: Path, **overrides: object) -> Path:
    manifest: dict[str, object] = {
        "source_uri": "bolt://localhost:7687",
        "source_database": "neo4j",
        "verses": 2,
        "verses_with_embedding": 2,
        "relationships": {"PART_OF": 2},
        "relationships_total": 2,
        "label_nodes": {"Scripture": 2},
        "embedding_dim": 384,
        "embedding_model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "vector_index": "verseEmbeddingIndex",
        "fulltext_index": "verseTextIndex",
    }
    manifest.update(overrides)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def configure_precision(monkeypatch: pytest.MonkeyPatch, precision: str) -> None:
    monkeypatch.setenv("GITAGUIDE_EMBEDDER_PRECISION", precision)
    reset_settings_cache()


# The manifest
def test_manifest_records_the_precision_the_check_measured() -> None:
    settings = Settings(mock_mode=True, embedder_precision="int8")
    check = {"precision": "int8", "encoder": "all-MiniLM-L6-v2-onnx-int8",
             "sampled": 12, "min_cosine": 1.0, "threshold": 0.999}

    manifest = export_neo4j.build_manifest(settings, 2, 2, RELATIONSHIPS, check)

    assert manifest["embedder_precision"] == "int8"
    assert manifest["embedder_precision_verified"] is True
    assert manifest["precision_check"] == check


def test_manifest_records_a_skipped_check_as_unverified() -> None:
    """A configured value is not a measured one, and the manifest must not blur them."""
    settings = Settings(mock_mode=True, embedder_precision="fp32")
    manifest = export_neo4j.build_manifest(settings, 2, 2, RELATIONSHIPS, None)

    assert manifest["embedder_precision"] == "fp32"
    assert manifest["embedder_precision_verified"] is False
    assert manifest["precision_check"]["skipped"] is True


def test_manifest_records_when_it_was_exported() -> None:
    settings = Settings(mock_mode=True)
    before = datetime.now(UTC).replace(microsecond=0)
    manifest = export_neo4j.build_manifest(settings, 2, 2, RELATIONSHIPS, None)

    exported_at = datetime.fromisoformat(manifest["exported_at"])
    assert exported_at.tzinfo is not None, "the timestamp must carry its zone"
    assert before <= exported_at <= datetime.now(UTC)


# The measurement
def test_the_same_encoder_agrees_to_one() -> None:
    stored = {"BG-2.47": [0.1, 0.2, 0.3], "AS-1.2.3": [0.3, 0.2, 0.1]}
    assert export_neo4j.min_cosine(stored, stored) == pytest.approx(1.0)


def test_a_different_encoder_is_below_the_threshold() -> None:
    """A perturbation the size of INT8-vs-fp32 drift (~0.986) must fail the bar."""
    stored = {"BG-2.47": [1.0, 0.0, 0.0], "AS-1.2.3": [0.0, 1.0, 0.0]}
    fresh = {"BG-2.47": [1.0, 0.0, 0.0], "AS-1.2.3": [0.0, 1.0, 0.17]}  # cosine ~0.986
    lowest = export_neo4j.min_cosine(stored, fresh)
    assert lowest < export_neo4j.PRECISION_MATCH_MIN_COSINE
    assert lowest == pytest.approx(0.986, abs=0.002)


def test_the_minimum_not_the_mean_decides() -> None:
    """Eleven perfect verses and one from another encoder is a mixed space."""
    stored = {f"BG-1.{i}": [1.0, 0.0] for i in range(12)}
    fresh = dict(stored)
    fresh["BG-1.11"] = [0.0, 1.0]
    assert export_neo4j.min_cosine(stored, fresh) == pytest.approx(0.0)


def test_nothing_in_common_is_an_error_not_a_pass() -> None:
    with pytest.raises(export_neo4j.PrecisionMismatchError):
        export_neo4j.min_cosine({"BG-1.1": [1.0]}, {"BG-1.2": [1.0]})


def test_the_check_refuses_to_run_on_the_hashing_embedder() -> None:
    """Mock mode would compare MiniLM vectors against hashed bags of words."""
    with pytest.raises(export_neo4j.PrecisionMismatchError, match="mock mode"):
        export_neo4j.verify_stored_precision(None, "neo4j", Settings(mock_mode=True))


# The import refuses what it cannot vouch for
def test_import_refuses_a_manifest_that_records_no_precision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_precision(monkeypatch, "int8")
    write_manifest(tmp_path)  # the pre-8-Sep shape

    assert import_neo4j.main(["--source", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "embedder_precision" in err
    assert "re-export" in err.lower()


def test_import_refuses_a_precision_that_differs_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 8 Sep case: fp32 vectors, an INT8 deployment, every count matching."""
    configure_precision(monkeypatch, "int8")
    write_manifest(tmp_path, embedder_precision="fp32", embedder_precision_verified=True)

    assert import_neo4j.main(["--source", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "fp32" in err and "int8" in err, "the message must name both sides"
    assert "GITAGUIDE_EMBEDDER_PRECISION" in err


def test_import_refuses_the_mismatch_even_for_verify_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_precision(monkeypatch, "fp32")
    write_manifest(tmp_path, embedder_precision="int8", embedder_precision_verified=True)

    assert import_neo4j.main(["--source", str(tmp_path), "--verify-only"]) == 2


def test_import_with_a_matching_precision_gets_as_far_as_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proves the precision check passed: the next failure is the missing password."""
    configure_precision(monkeypatch, "int8")
    write_manifest(tmp_path, embedder_precision="int8", embedder_precision_verified=True)

    assert import_neo4j.main(["--source", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "GITAGUIDE_NEO4J_PASSWORD" in err
    assert "encoder" not in err


def test_an_unverified_manifest_is_loud_but_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--skip-precision-check is for synthetic graphs; the import says so and continues."""
    configure_precision(monkeypatch, "fp32")
    write_manifest(tmp_path, embedder_precision="fp32", embedder_precision_verified=False)

    assert import_neo4j.main(["--source", str(tmp_path)]) == 2  # then: no password
    err = capsys.readouterr().err
    assert "[WARN]" in err and "not what it measured" in err
    assert "GITAGUIDE_NEO4J_PASSWORD" in err


@pytest.mark.parametrize(
    ("recorded", "configured", "expect_problem"),
    [("int8", "int8", False), ("fp32", "fp32", False), ("fp32", "int8", True),
     ("int8", "fp32", True), (None, "int8", True)],
)
def test_manifest_precision_problem(recorded: str | None, configured: str, expect_problem: bool) -> None:
    manifest = {} if recorded is None else {"embedder_precision": recorded}
    settings = Settings(mock_mode=True, embedder_precision=configured)
    problem = import_neo4j.manifest_precision_problem(manifest, settings)
    assert (problem is not None) is expect_problem
