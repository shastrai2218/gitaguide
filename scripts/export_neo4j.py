"""Export the knowledge graph as data, for :mod:`scripts.import_neo4j` to load.

Why an export rather than a rebuild
-----------------------------------
The graph could be rebuilt from the corpus against the target directly, but that
would re-run the tagger and the hub-suppression pass, producing a *different*
graph that merely looks similar. Every figure in ``docs/kg_measurements.md`` was
measured against the graph as it exists locally, so exporting moves that exact
artefact and production is the deployment the evidence describes.

Why JSON and not Cypher
-----------------------
This script used to emit ``.cypher`` files built by interpolating
``json.dumps()`` output into ``UNWIND [...] AS row``. That is invalid Cypher:
JSON quotes its map keys (``{"verse_id": ...}``) and Cypher requires bare
identifiers (``{verse_id: ...}``). Every generated file was rejected by
``cypher-shell`` at line 1, and it was only discovered during a real AuraDB
import - the writer had been "tested" by reading what it produced, which is no
test at all.

The lesson is not "escape more carefully". It is that **hand-built query text is
the wrong tool**. This now writes plain data, and the importer sends it as
query *parameters*, where the driver handles typing and escaping and no amount
of unusual content in a verse can change the shape of a statement.

Which encoder wrote the vectors
-------------------------------
The stored vectors and the serving encoder must be the same encoder: an INT8
query against fp32-built vectors does not error, it quietly returns worse
neighbours (0.986 min cosine, measured). An export made on 6 Sep 2026 carried
fp32 vectors while production was about to switch to INT8, and nothing in the
manifest could say so - the counts all matched.

So the export now **measures** which encoder wrote the graph before it writes a
single file: it re-embeds a sample of verses with the configured encoder and
requires the stored vectors to agree to cosine >= 0.999 (the same encoder gives
1.000000; the two precisions of the same MiniLM give ~0.986). The result goes
into the manifest as ``embedder_precision``, and :mod:`scripts.import_neo4j`
refuses to load a manifest whose precision differs from the environment it is
importing for. ``--skip-precision-check`` exists for synthetic graphs whose
vectors were never produced by any encoder (the round-trip test fixture); the
manifest then says the precision was configured, not verified.

Output
------
=========================== ==================================================
``manifest.json``           counts, index config, encoder precision, export
                            time - the import is verified against this
``verses.jsonl``            one verse per line, embeddings included (~33 MB)
``relationships.json``      label/Scripture nodes and every edge, by type
=========================== ==================================================

Usage
-----
::

    python -m scripts.export_neo4j --out data/graph_export

Reads the source from ``GITAGUIDE_NEO4J_*`` and the encoder from
``GITAGUIDE_EMBEDDER_PRECISION``. Read-only, and it never prints a credential.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

CORPUS_DATA_DIR = Path(__file__).resolve().parent.parent / "corpus" / "data"
DEFAULT_CORPUS_PATHS = (
    str(CORPUS_DATA_DIR / "gita.json"),
    str(CORPUS_DATA_DIR / "arthashastra.json"),
)

#: Lowest cosine between a stored vector and the same text re-embedded with the
#: configured encoder for the two to count as *the same encoder*. Identical
#: encoders agree to 1.000000; the INT8 and fp32 exports of the same MiniLM
#: agree only to 0.985-0.994 (twelve verses, 8 Sep 2026). 0.999 sits between
#: those with room on both sides.
PRECISION_MATCH_MIN_COSINE = 0.999
#: Verses re-embedded for the check. Evenly spaced through the id order, so the
#: sample spans both scriptures without being random.
PRECISION_SAMPLE_SIZE = 12


class ExportIncompleteError(RuntimeError):
    """The export would not reproduce the source graph."""


class PrecisionMismatchError(RuntimeError):
    """The stored vectors were not written by the configured encoder."""


#: ``(node label, relationship type, key property)`` for the label layers.
#:
#: Read from the graph, never guessed. An earlier version assumed the concept
#: edge was ``MENTIONS_CONCEPT`` when it is ``MENTIONS``; the query matched
#: nothing, the export "succeeded", and 740 edges would have been missing in
#: production. The completeness check below exists because of that.
LABEL_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("Emotion", "ADDRESSES_EMOTION", "name"),
    ("Theme", "HAS_THEME", "name"),
    ("Concept", "MENTIONS", "name"),
)

VERSE_QUERY = """
MATCH (v:Verse)
RETURN v.id AS id, v.source AS source, v.chapter AS chapter,
       v.verse AS verse, v.book AS book,
       v.text_english AS text_english, v.text_sanskrit AS text_sanskrit,
       v.transliteration AS transliteration, v.commentary AS commentary,
       v.citation AS citation, v.embedding AS embedding
ORDER BY v.id
"""


def fetch(driver: Any, database: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(cypher, params, database_=database)
    return [record.data() for record in records]


# --------------------------------------------------------------------------- #
# Which encoder wrote the vectors
# --------------------------------------------------------------------------- #
def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0


def min_cosine(
    stored: Mapping[str, Sequence[float]], fresh: Mapping[str, Sequence[float]]
) -> float:
    """Lowest agreement over the ids both sides carry.

    The minimum, not the mean: one verse embedded by a different encoder is
    already a mixed vector space, and a mean would average it away.
    """
    shared = sorted(set(stored) & set(fresh))
    if not shared:
        raise PrecisionMismatchError("no verse ids in common; nothing to compare")
    return min(cosine(stored[verse_id], fresh[verse_id]) for verse_id in shared)


def verify_stored_precision(
    driver: Any,
    database: str,
    settings: Settings,
    corpus_paths: Sequence[str] = DEFAULT_CORPUS_PATHS,
    sample: int = PRECISION_SAMPLE_SIZE,
) -> dict[str, Any]:
    """Prove the graph's vectors came from the configured encoder.

    Re-embeds ``sample`` verses - the same ``Verse.embedding_text()`` the graph
    builder and :mod:`scripts.reembed_graph` use - with the encoder
    ``GITAGUIDE_EMBEDDER_PRECISION`` selects, and compares against what the
    graph holds. Returns the measurement for the manifest, or raises
    :class:`PrecisionMismatchError` so no file is written with a label the
    vectors do not deserve.
    """
    if settings.mock_mode:
        raise PrecisionMismatchError(
            "mock mode selects the hashing embedder, whose vectors are unrelated to "
            "MiniLM's, so the check cannot run. Set GITAGUIDE_MOCK_MODE=false, or pass "
            "--skip-precision-check for a synthetic graph."
        )

    from app.embeddings import build_embedder
    from corpus.schemas import Corpus

    corpus = Corpus()
    for raw in corpus_paths:
        path = Path(raw)
        if not path.exists():
            raise PrecisionMismatchError(
                f"corpus file not found: {path}. The check re-embeds verse text from the "
                "corpus files (--corpus); without them it cannot say which encoder wrote "
                "the graph."
            )
        corpus.extend(Corpus.load(path))
    by_id = {verse.id: verse for verse in corpus}

    embedded_ids = [
        row["id"]
        for row in fetch(
            driver,
            database,
            "MATCH (v:Verse) WHERE v.embedding IS NOT NULL RETURN v.id AS id ORDER BY v.id",
        )
        if row["id"] in by_id
    ]
    if not embedded_ids:
        raise PrecisionMismatchError(
            "none of the graph's embedded verses appear in the corpus files, so the "
            "check has no text to re-embed. Is this graph built from this corpus?"
        )
    step = max(1, len(embedded_ids) // sample)
    chosen = embedded_ids[::step][:sample]

    stored = {
        row["id"]: row["embedding"]
        for row in fetch(
            driver,
            database,
            "MATCH (v:Verse) WHERE v.id IN $ids RETURN v.id AS id, v.embedding AS embedding",
            ids=chosen,
        )
    }
    embedder = build_embedder(settings)
    fresh = dict(
        zip(
            chosen,
            embedder.embed([by_id[verse_id].embedding_text() for verse_id in chosen]),
            strict=True,
        )
    )
    lowest = min_cosine(stored, fresh)
    report = {
        "precision": settings.embedder_precision,
        "encoder": embedder.name,
        "sampled": len(chosen),
        "min_cosine": round(lowest, 6),
        "threshold": PRECISION_MATCH_MIN_COSINE,
    }
    if lowest < PRECISION_MATCH_MIN_COSINE:
        raise PrecisionMismatchError(
            f"the graph's vectors were not written by the {settings.embedder_precision} "
            f"encoder: re-embedding {len(chosen)} verses with {embedder.name} agrees with the "
            f"stored vectors only to min cosine {lowest:.6f} (the same encoder gives "
            f"1.000000; fp32 against int8 gives ~0.986). Either GITAGUIDE_EMBEDDER_PRECISION "
            "is wrong for this graph, or the graph needs `python -m scripts.reembed_graph` at "
            "the intended precision. Refusing to write a manifest that would label the "
            "vectors wrongly."
        )
    return report


def export_verses(driver: Any, database: str, path: Path) -> tuple[int, int]:
    """Write one verse per line. Returns ``(verses, verses_with_embedding)``."""
    rows = fetch(driver, database, VERSE_QUERY)
    embedded = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if row.get("embedding"):
                embedded += 1
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows), embedded


def export_relationships(driver: Any, database: str) -> dict[str, Any]:
    """Every edge and every label node, grouped by type."""
    payload: dict[str, Any] = {"label_nodes": {}, "edges": {}}

    for label, rel_type, key in LABEL_LAYERS:
        payload["edges"][rel_type] = fetch(
            driver,
            database,
            f"MATCH (v:Verse)-[:{rel_type}]->(n:{label}) "
            f"RETURN v.id AS verse_id, n.{key} AS name ORDER BY v.id, n.{key}",
        )

    payload["label_nodes"]["Scripture"] = fetch(
        driver,
        database,
        "MATCH (s:Scripture) RETURN s.key AS key, s.name AS name ORDER BY s.key",
    )
    payload["edges"]["PART_OF"] = fetch(
        driver,
        database,
        "MATCH (v:Verse)-[:PART_OF]->(s:Scripture) "
        "RETURN v.id AS verse_id, s.key AS scripture_key ORDER BY v.id",
    )
    payload["edges"]["RELATED_TO"] = fetch(
        driver,
        database,
        "MATCH (a:Verse)-[r:RELATED_TO]->(b:Verse) "
        "RETURN a.id AS from_id, b.id AS to_id, "
        "coalesce(r.weight, 1.0) AS weight, coalesce(r.shared, []) AS shared "
        "ORDER BY a.id, b.id",
    )

    # ---- completeness: refuse to export a partial graph -------------------
    #
    # Fail loudly rather than write a file quietly missing a relationship type.
    # A partial import would leave production retrieving against a different
    # graph from the one every measurement was taken on, and nothing downstream
    # would notice.
    present = {
        row["relationshipType"]
        for row in fetch(driver, database, "CALL db.relationshipTypes()")
    }
    handled = set(payload["edges"])
    missing = present - handled
    if missing:
        raise ExportIncompleteError(
            f"the graph contains relationship type(s) this exporter does not "
            f"handle: {sorted(missing)}. Exporting anyway would silently drop "
            f"them. Add them to LABEL_LAYERS or export_relationships() in "
            f"scripts/export_neo4j.py."
        )
    empty = sorted(name for name, rows in payload["edges"].items() if not rows)
    if empty:
        raise ExportIncompleteError(
            f"relationship type(s) exported zero edges: {empty}. Either the "
            "query name is wrong or the graph is not built; both are worth "
            "stopping for. Check with: CALL db.relationshipTypes()"
        )
    return payload


def build_manifest(
    settings: Settings,
    verses: int,
    embedded: int,
    relationships: dict[str, Any],
    precision_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The contract the import is verified against.

    ``embedder_precision`` is what the import compares against its own
    environment. When ``precision_check`` is ``None`` the check was skipped, and
    ``embedder_precision_verified`` says so: the precision is then what the
    exporter was *configured* with, not what it measured.
    """
    edge_counts = {name: len(rows) for name, rows in relationships["edges"].items()}
    return {
        "source_uri": settings.neo4j_uri,
        "source_database": settings.neo4j_database,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verses": verses,
        "verses_with_embedding": embedded,
        "relationships": edge_counts,
        "relationships_total": sum(edge_counts.values()),
        "label_nodes": {
            label: len(rows) for label, rows in relationships["label_nodes"].items()
        },
        "embedding_dim": settings.embedding_dim,
        "embedding_model_id": settings.embedding_model_id,
        "embedder_precision": settings.embedder_precision,
        "embedder_precision_verified": precision_check is not None,
        "precision_check": precision_check
        or {"skipped": True, "reason": "--skip-precision-check"},
        "vector_index": settings.neo4j_vector_index,
        "fulltext_index": settings.neo4j_fulltext_index,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/graph_export", help="output directory")
    parser.add_argument(
        "--corpus",
        nargs="+",
        default=list(DEFAULT_CORPUS_PATHS),
        help="corpus files whose verse text the precision check re-embeds",
    )
    parser.add_argument(
        "--skip-precision-check",
        action="store_true",
        help=(
            "do not verify which encoder wrote the vectors. Only for synthetic graphs "
            "whose embeddings no encoder produced; the manifest records the check as skipped"
        ),
    )
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    if not settings.neo4j_password:
        print("[FAIL] no GITAGUIDE_NEO4J_PASSWORD set; nothing to export from", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The host, never the credential - so the operator can see they are
    # exporting from the machine they think they are.
    print(f"exporting from {settings.neo4j_uri} (database={settings.neo4j_database})")
    print(f"  encoder configured    : {settings.embedder_precision}")

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    try:
        driver.verify_connectivity()

        # Before any file is written: prove the vectors are what the manifest
        # is about to say they are. A wrong label here is the one failure the
        # importer's count checks cannot see.
        precision_check: dict[str, Any] | None = None
        if args.skip_precision_check:
            print(
                "  encoder verification  : SKIPPED (--skip-precision-check); the manifest "
                "records the configured precision as unverified",
                file=sys.stderr,
            )
        else:
            precision_check = verify_stored_precision(
                driver, settings.neo4j_database, settings, args.corpus
            )
            print(
                f"  encoder verified      : {precision_check['encoder']} "
                f"(min cosine {precision_check['min_cosine']:.6f} over "
                f"{precision_check['sampled']} verses)"
            )

        verses, embedded = export_verses(
            driver, settings.neo4j_database, out_dir / "verses.jsonl"
        )
        print(f"  verses.jsonl          {verses} verses ({embedded} embedded)")
        if embedded != verses:
            print(
                f"[WARN] {verses - embedded} verse(s) have no embedding and will not "
                "be reachable by the vector channel",
                file=sys.stderr,
            )

        relationships = export_relationships(driver, settings.neo4j_database)
        (out_dir / "relationships.json").write_text(
            json.dumps(relationships, ensure_ascii=False), encoding="utf-8"
        )
        for name, rows in relationships["edges"].items():
            print(f"  relationships.json    {name}: {len(rows)}")

        manifest = build_manifest(settings, verses, embedded, relationships, precision_check)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(
            f"  manifest.json         {manifest['relationships_total']} edges total, "
            f"embedder_precision={manifest['embedder_precision']}, "
            f"exported_at={manifest['exported_at']}"
        )
        print(f"\nwritten to {out_dir.resolve()}")
        print("next: python -m scripts.import_neo4j --source " + str(out_dir))
    except PrecisionMismatchError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
