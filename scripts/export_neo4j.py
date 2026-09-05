"""Export the local knowledge graph for import into Neo4j AuraDB.

Why an export rather than a rebuild
-----------------------------------
The graph could be rebuilt from the corpus against Aura directly, but that would
re-embed 3,836 verses and re-derive every ``RELATED_TO`` edge, and the result
would not be *the same graph* - embeddings are deterministic, but a rebuild also
re-runs the tagger and the hub-suppression pass. Every number in
``docs/kg_measurements.md`` was measured against the graph as it exists locally.
Exporting it moves that exact artefact, so the production deployment is the one
the evidence describes.

What it writes
--------------
Cypher, in three files, in the order they must be run:

1. ``01_constraints.cypher`` - constraints and indexes, including the vector
   index. Created **first**: creating them after the data means every node is
   scanned twice.
2. ``02_nodes.cypher``       - ``:Verse`` nodes with their properties and their
   384-dimension embeddings, in batched ``UNWIND`` statements.
3. ``03_relationships.cypher`` - label nodes and every edge.

Plain Cypher rather than ``neo4j-admin database dump`` because Aura Free does
not accept a dump upload; the console's import tooling takes Cypher, and this
file can also be replayed by ``cypher-shell`` against anything.

Usage
-----
::

    python -m scripts.export_neo4j --out data/graph_export
    python -m scripts.export_neo4j --out data/graph_export --batch-size 200

Reads the source graph from ``GITAGUIDE_NEO4J_*``. It is **read-only**: it runs
no write query against the source, and it never prints a credential.
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


#: Aura rejects very large single statements; 100 verses per UNWIND keeps each
#: statement well inside the limit while still being far faster than one
#: statement per node.
DEFAULT_BATCH_SIZE = 100

CONSTRAINTS = """// GitaGuide - constraints and indexes.
// Run this FIRST, before the node and relationship files.

CREATE CONSTRAINT verse_id IF NOT EXISTS
FOR (v:Verse) REQUIRE v.id IS UNIQUE;

CREATE CONSTRAINT emotion_name IF NOT EXISTS
FOR (e:Emotion) REQUIRE e.name IS UNIQUE;

CREATE CONSTRAINT theme_name IF NOT EXISTS
FOR (t:Theme) REQUIRE t.name IS UNIQUE;

CREATE CONSTRAINT concept_name IF NOT EXISTS
FOR (c:Concept) REQUIRE c.name IS UNIQUE;

// Vector index. The dimension and the similarity function must match the
// embedder exactly: all-MiniLM-L6-v2, 384 dimensions, cosine. A mismatch here
// does not error - it silently returns poor neighbours.
CREATE VECTOR INDEX {vector_index} IF NOT EXISTS
FOR (v:Verse) ON (v.embedding)
OPTIONS {{indexConfig: {{
  `vector.dimensions`: {dimensions},
  `vector.similarity_function`: 'cosine'
}}}};

CREATE FULLTEXT INDEX {fulltext_index} IF NOT EXISTS
FOR (n:Verse) ON EACH [n.text_english, n.commentary];
"""


def cypher_value(value: Any) -> str:
    """Render a Python value as a Cypher literal.

    ``json.dumps`` is close enough to Cypher's map/list/string syntax to be
    correct here and, unlike hand-rolled quoting, it escapes the Sanskrit and
    Devanagari text properly.
    """
    return json.dumps(value, ensure_ascii=False)


def fetch(driver: Any, database: str, cypher: str, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(cypher, params, database_=database)
    return [record.data() for record in records]


def export_nodes(driver: Any, database: str, batch_size: int) -> tuple[str, int]:
    """``:Verse`` nodes, batched. Embeddings dominate the file size."""
    rows = fetch(
        driver,
        database,
        """
        MATCH (v:Verse)
        RETURN v.id AS id, v.source AS source, v.chapter AS chapter,
               v.verse AS verse, v.book AS book,
               v.text_english AS text_english, v.text_sanskrit AS text_sanskrit,
               v.transliteration AS transliteration, v.commentary AS commentary,
               v.citation AS citation, v.embedding AS embedding
        ORDER BY v.id
        """,
    )

    lines = [
        "// GitaGuide - :Verse nodes. Run AFTER 01_constraints.cypher.",
        f"// {len(rows)} verses, batched {batch_size} per statement.",
        "",
    ]
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        lines.append(f"UNWIND {cypher_value(batch)} AS row")
        lines.append("MERGE (v:Verse {id: row.id})")
        lines.append(
            "SET v.source = row.source, v.chapter = row.chapter, v.verse = row.verse,\n"
            "    v.book = row.book, v.text_english = row.text_english,\n"
            "    v.text_sanskrit = row.text_sanskrit,\n"
            "    v.transliteration = row.transliteration,\n"
            "    v.commentary = row.commentary, v.citation = row.citation,\n"
            "    v.embedding = row.embedding;"
        )
        lines.append("")
    return "\n".join(lines), len(rows)


def export_relationships(driver: Any, database: str, batch_size: int) -> tuple[str, dict[str, int]]:
    """Label nodes and every edge, one section per relationship type."""
    lines = [
        "// GitaGuide - label nodes and relationships.",
        "// Run AFTER 02_nodes.cypher.",
        "",
    ]
    counts: dict[str, int] = {}

    # Relationship names are read from the graph, not assumed. The first
    # version of this script guessed ``MENTIONS_CONCEPT`` and exported zero
    # concept edges: the query simply matched nothing, the export "succeeded",
    # and 740 edges would have been missing in production with no error
    # anywhere. Hence the completeness check at the end of this function.
    label_specs = (
        ("Emotion", "ADDRESSES_EMOTION", "name"),
        ("Theme", "HAS_THEME", "name"),
        ("Concept", "MENTIONS", "name"),
    )
    for label, rel_type, key in label_specs:
        rows = fetch(
            driver,
            database,
            f"MATCH (v:Verse)-[:{rel_type}]->(n:{label}) "
            f"RETURN v.id AS verse_id, n.{key} AS name ORDER BY v.id, n.{key}",
        )
        counts[rel_type] = len(rows)
        if not rows:
            continue
        lines.append(f"// ---- {rel_type} ({len(rows)} edges) ----")
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            lines.append(f"UNWIND {cypher_value(batch)} AS row")
            lines.append(f"MERGE (n:{label} {{{key}: row.name}})")
            lines.append("WITH n, row MATCH (v:Verse {id: row.verse_id})")
            lines.append(f"MERGE (v)-[:{rel_type}]->(n);")
            lines.append("")

    # Scripture nodes and PART_OF, which carry the corpus membership.
    scriptures = fetch(
        driver,
        database,
        "MATCH (s:Scripture) RETURN s.key AS key, s.name AS name ORDER BY s.key",
    )
    if scriptures:
        lines.append(f"// ---- Scripture nodes ({len(scriptures)}) ----")
        lines.append(f"UNWIND {cypher_value(scriptures)} AS row")
        lines.append("MERGE (s:Scripture {key: row.key}) SET s.name = row.name;")
        lines.append("")

    part_of = fetch(
        driver,
        database,
        "MATCH (v:Verse)-[:PART_OF]->(s:Scripture) "
        "RETURN v.id AS verse_id, s.key AS scripture_key ORDER BY v.id",
    )
    counts["PART_OF"] = len(part_of)
    if part_of:
        lines.append(f"// ---- PART_OF ({len(part_of)} edges) ----")
        for start in range(0, len(part_of), batch_size):
            batch = part_of[start : start + batch_size]
            lines.append(f"UNWIND {cypher_value(batch)} AS row")
            lines.append("MATCH (v:Verse {id: row.verse_id})")
            lines.append("MERGE (s:Scripture {key: row.scripture_key})")
            lines.append("MERGE (v)-[:PART_OF]->(s);")
            lines.append("")

    related = fetch(
        driver,
        database,
        "MATCH (a:Verse)-[r:RELATED_TO]->(b:Verse) "
        "RETURN a.id AS from_id, b.id AS to_id, "
        "coalesce(r.weight, 1.0) AS weight, coalesce(r.shared, []) AS shared "
        "ORDER BY a.id, b.id",
    )
    counts["RELATED_TO"] = len(related)
    if related:
        lines.append(f"// ---- RELATED_TO ({len(related)} edges, hub-suppressed) ----")
        for start in range(0, len(related), batch_size):
            batch = related[start : start + batch_size]
            lines.append(f"UNWIND {cypher_value(batch)} AS row")
            lines.append("MATCH (a:Verse {id: row.from_id}), (b:Verse {id: row.to_id})")
            lines.append("MERGE (a)-[r:RELATED_TO]->(b)")
            lines.append("SET r.weight = row.weight, r.shared = row.shared;")
            lines.append("")

    # ---- completeness: refuse to export a partial graph -----------------
    #
    # Fail loudly rather than write a file that is quietly missing a
    # relationship type. A partial import would leave production retrieving
    # against a different graph from the one every measurement was taken on,
    # and nothing downstream would notice.
    present = {
        row["relationshipType"]
        for row in fetch(driver, database, "CALL db.relationshipTypes()")
    }
    missing = present - set(counts)
    if missing:
        raise ExportIncompleteError(
            f"the graph contains relationship type(s) this exporter does not "
            f"handle: {sorted(missing)}. Exporting anyway would silently drop "
            f"them. Add them to export_relationships() in scripts/export_neo4j.py."
        )

    empty = sorted(name for name, count in counts.items() if count == 0)
    if empty:
        raise ExportIncompleteError(
            f"relationship type(s) exported zero edges: {empty}. Either the "
            "query name is wrong or the graph is not built; both are worth "
            "stopping for. Check with: CALL db.relationshipTypes()"
        )

    return "\n".join(lines), counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/graph_export", help="output directory")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    if not settings.neo4j_password:
        print("[FAIL] no GITAGUIDE_NEO4J_PASSWORD set; nothing to export from", file=sys.stderr)
        return 2

    from neo4j import GraphDatabase

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Credentials are never printed - only the host, so the operator can see
    # they are exporting from the machine they think they are.
    print(f"exporting from {settings.neo4j_uri} (database={settings.neo4j_database})")

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    try:
        driver.verify_connectivity()

        constraints = CONSTRAINTS.format(
            vector_index=settings.neo4j_vector_index,
            fulltext_index=settings.neo4j_fulltext_index,
            dimensions=settings.embedding_dim,
        )
        (out_dir / "01_constraints.cypher").write_text(constraints, encoding="utf-8")
        print(f"  01_constraints.cypher   indexes + constraints (dim={settings.embedding_dim})")

        nodes, verse_count = export_nodes(driver, settings.neo4j_database, args.batch_size)
        (out_dir / "02_nodes.cypher").write_text(nodes, encoding="utf-8")
        print(f"  02_nodes.cypher         {verse_count} verses")

        rels, counts = export_relationships(driver, settings.neo4j_database, args.batch_size)
        (out_dir / "03_relationships.cypher").write_text(rels, encoding="utf-8")
        for rel_type, count in counts.items():
            print(f"  03_relationships.cypher {rel_type}: {count}")

        embedded = fetch(
            driver,
            settings.neo4j_database,
            "MATCH (v:Verse) WHERE v.embedding IS NOT NULL RETURN count(v) AS n",
        )[0]["n"]
        if embedded != verse_count:
            print(
                f"[WARN] {verse_count - embedded} verse(s) have no embedding and will "
                "not be retrievable by the vector channel",
                file=sys.stderr,
            )

        manifest = {
            "source_uri": settings.neo4j_uri,
            "database": settings.neo4j_database,
            "verses": verse_count,
            "verses_with_embedding": embedded,
            "relationships": counts,
            "embedding_dim": settings.embedding_dim,
            "embedding_model_id": settings.embedding_model_id,
            "vector_index": settings.neo4j_vector_index,
            "fulltext_index": settings.neo4j_fulltext_index,
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print("  manifest.json           verify the import against this\n")
        print(f"written to {out_dir.resolve()}")
        print("next: docs/DEPLOY.md step 1 (AuraDB import)")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
