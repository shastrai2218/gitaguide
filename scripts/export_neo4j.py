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

Output
------
=========================== ==================================================
``manifest.json``           counts, index config, provenance - the import is
                            verified against this
``verses.jsonl``            one verse per line, embeddings included (~33 MB)
``relationships.json``      label/Scripture nodes and every edge, by type
=========================== ==================================================

Usage
-----
::

    python -m scripts.export_neo4j --out data/graph_export

Reads the source from ``GITAGUIDE_NEO4J_*``. Read-only, and it never prints a
credential.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings


class ExportIncompleteError(RuntimeError):
    """The export would not reproduce the source graph."""


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
    settings: Settings, verses: int, embedded: int, relationships: dict[str, Any]
) -> dict[str, Any]:
    """The contract the import is verified against."""
    edge_counts = {name: len(rows) for name, rows in relationships["edges"].items()}
    return {
        "source_uri": settings.neo4j_uri,
        "source_database": settings.neo4j_database,
        "verses": verses,
        "verses_with_embedding": embedded,
        "relationships": edge_counts,
        "relationships_total": sum(edge_counts.values()),
        "label_nodes": {
            label: len(rows) for label, rows in relationships["label_nodes"].items()
        },
        "embedding_dim": settings.embedding_dim,
        "embedding_model_id": settings.embedding_model_id,
        "vector_index": settings.neo4j_vector_index,
        "fulltext_index": settings.neo4j_fulltext_index,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/graph_export", help="output directory")
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

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    try:
        driver.verify_connectivity()

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

        manifest = build_manifest(settings, verses, embedded, relationships)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(f"  manifest.json         {manifest['relationships_total']} edges total")
        print(f"\nwritten to {out_dir.resolve()}")
        print("next: python -m scripts.import_neo4j --source " + str(out_dir))
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
