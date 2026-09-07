"""Load an exported graph into Neo4j (AuraDB or local), idempotently.

Replaces the generated-``.cypher``-file approach, which produced syntactically
invalid Cypher: it interpolated ``json.dumps()`` into ``UNWIND [...] AS row``,
and JSON quotes its map keys where Cypher requires bare identifiers. Every file
was rejected at line 1 by ``cypher-shell``, and nothing caught it until a real
AuraDB import.

So this sends **parameters, not text**. Every statement here is a fixed string
with ``$rows`` in it; the driver types and escapes the payload. Sanskrit,
Devanagari, quotes, 384-float vectors - none of it can change the shape of a
statement, because none of it is ever concatenated into one.

Safe to re-run
--------------
Every write is a ``MERGE`` on a stable key (``Verse.id``, ``Emotion.name``,
``Scripture.key``, and the endpoint pair for ``RELATED_TO``), and constraints
and indexes use ``IF NOT EXISTS``. A half-finished import is fixed by running
it again, which matters when the network drops 20 MB into a 33 MB upload.

Verified, not assumed
---------------------
It finishes by counting what is actually in the target and comparing against
``manifest.json``. A mismatch is a hard failure with the difference printed -
a partially imported graph is the failure mode that would otherwise reach
production looking healthy.

Usage
-----
::

    # credentials come from the environment; this script never asks for them
    export GITAGUIDE_NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
    export GITAGUIDE_NEO4J_USERNAME=xxxxxxxx
    export GITAGUIDE_NEO4J_PASSWORD=...
    export GITAGUIDE_NEO4J_DATABASE=xxxxxxxx

    python -m scripts.import_neo4j --source data/graph_export
    python -m scripts.import_neo4j --source data/graph_export --verify-only

On **AuraDB Free the username and database are both the instance id**, not
``neo4j`` - see docs/DATA_MIGRATION.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings


class ImportVerificationError(RuntimeError):
    """The target graph does not match the manifest."""


def manifest_precision_problem(manifest: dict[str, Any], settings: Settings) -> str | None:
    """Why this manifest must not be imported for this environment, or ``None``.

    The vectors in an export were written by one encoder; the service that will
    query them is configured with ``GITAGUIDE_EMBEDDER_PRECISION``. If those
    differ, the import succeeds, every count matches, and retrieval quietly
    returns worse neighbours (0.986 min cosine, measured). This happened once
    already on 8 Sep 2026: an fp32 export sat ready to load into an INT8
    deployment and nothing in the manifest could say so. So the operator's
    environment has to state the precision production runs, and the manifest
    has to agree with it.
    """
    recorded = manifest.get("embedder_precision")
    if recorded is None:
        return (
            "manifest.json records no embedder_precision, so nothing says which encoder "
            "wrote its vectors (exports before 8 Sep 2026 did not record it). Re-export "
            "with the current scripts/export_neo4j.py, which measures it."
        )
    if recorded != settings.embedder_precision:
        return (
            f"the export's vectors were written by the {recorded} encoder, but this "
            f"environment has GITAGUIDE_EMBEDDER_PRECISION={settings.embedder_precision}. "
            "Importing would put one encoder's vectors behind a service that queries with "
            "another: no error anywhere, just worse neighbours. Set "
            f"GITAGUIDE_EMBEDDER_PRECISION={recorded} for this import if that is what "
            "production runs, or re-embed and re-export at the precision it does."
        )
    return None


#: Verses per statement. Each carries a 384-float embedding, so 500 keeps a
#: batch around 1.5 MB - large enough to be fast, small enough that a retry
#: after a dropped connection is cheap.
DEFAULT_BATCH_SIZE = 500

CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT verse_id IF NOT EXISTS FOR (v:Verse) REQUIRE v.id IS UNIQUE",
    "CREATE CONSTRAINT emotion_name IF NOT EXISTS FOR (e:Emotion) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT theme_name IF NOT EXISTS FOR (t:Theme) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT scripture_key IF NOT EXISTS FOR (s:Scripture) REQUIRE s.key IS UNIQUE",
)

#: Vector index config must match the embedder exactly. A wrong dimension or
#: similarity function does not error - it silently returns poor neighbours,
#: which is indistinguishable from a bad reranker.
VECTOR_INDEX = """
CREATE VECTOR INDEX {name} IF NOT EXISTS
FOR (v:Verse) ON (v.embedding)
OPTIONS {{indexConfig: {{
  `vector.dimensions`: $dimensions,
  `vector.similarity_function`: 'cosine'
}}}}
"""

FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX {name} IF NOT EXISTS
FOR (n:Verse) ON EACH [n.text_english, n.commentary]
"""

MERGE_VERSES = """
UNWIND $rows AS row
MERGE (v:Verse {id: row.id})
SET v.source = row.source,
    v.chapter = row.chapter,
    v.verse = row.verse,
    v.book = row.book,
    v.text_english = row.text_english,
    v.text_sanskrit = row.text_sanskrit,
    v.transliteration = row.transliteration,
    v.commentary = row.commentary,
    v.citation = row.citation,
    v.embedding = row.embedding
"""

MERGE_SCRIPTURES = """
UNWIND $rows AS row
MERGE (s:Scripture {key: row.key})
SET s.name = row.name
"""

MERGE_PART_OF = """
UNWIND $rows AS row
MATCH (v:Verse {id: row.verse_id})
MERGE (s:Scripture {key: row.scripture_key})
MERGE (v)-[:PART_OF]->(s)
"""

MERGE_RELATED_TO = """
UNWIND $rows AS row
MATCH (a:Verse {id: row.from_id})
MATCH (b:Verse {id: row.to_id})
MERGE (a)-[r:RELATED_TO]->(b)
SET r.weight = row.weight, r.shared = row.shared
"""

#: Label-layer edges. The label and relationship type are structural, so they
#: are formatted in from a fixed allow-list; the *data* is always a parameter.
MERGE_LABEL_EDGES = """
UNWIND $rows AS row
MERGE (n:{label} {{name: row.name}})
WITH n, row
MATCH (v:Verse {{id: row.verse_id}})
MERGE (v)-[:{rel_type}]->(n)
"""

#: Must match ``scripts.export_neo4j.LABEL_LAYERS``.
LABEL_LAYERS: tuple[tuple[str, str], ...] = (
    ("Emotion", "ADDRESSES_EMOTION"),
    ("Theme", "HAS_THEME"),
    ("Concept", "MENTIONS"),
)


def batched(rows: list[dict[str, Any]], size: int):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load_verses(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class Importer:
    def __init__(self, driver: Any, database: str, batch_size: int) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = batch_size

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(
            cypher, params, database_=self.database
        )
        return [record.data() for record in records]

    # ----------------------------------------------------------------- schema
    def apply_schema(self, settings: Settings) -> None:
        print("schema")
        for statement in CONSTRAINTS:
            self.run(statement)
        print(f"  {len(CONSTRAINTS)} constraints ensured")

        self.run(
            VECTOR_INDEX.format(name=settings.neo4j_vector_index),
            dimensions=settings.embedding_dim,
        )
        print(f"  vector index {settings.neo4j_vector_index} (dim={settings.embedding_dim})")
        self.run(FULLTEXT_INDEX.format(name=settings.neo4j_fulltext_index))
        print(f"  fulltext index {settings.neo4j_fulltext_index}")

    # ------------------------------------------------------------------ data
    def load_verses(self, rows: list[dict[str, Any]]) -> None:
        print(f"verses ({len(rows)})")
        done = 0
        for batch in batched(rows, self.batch_size):
            self.run(MERGE_VERSES, rows=batch)
            done += len(batch)
            print(f"  {done}/{len(rows)}", end="\r", flush=True)
        print(f"  {done}/{len(rows)} loaded   ")

    def load_relationships(self, payload: dict[str, Any]) -> None:
        scriptures = payload["label_nodes"].get("Scripture", [])
        if scriptures:
            self.run(MERGE_SCRIPTURES, rows=scriptures)
            print(f"Scripture nodes ({len(scriptures)})")

        for label, rel_type in LABEL_LAYERS:
            rows = payload["edges"].get(rel_type, [])
            if not rows:
                continue
            statement = MERGE_LABEL_EDGES.format(label=label, rel_type=rel_type)
            for batch in batched(rows, self.batch_size):
                self.run(statement, rows=batch)
            print(f"{rel_type} ({len(rows)})")

        part_of = payload["edges"].get("PART_OF", [])
        for batch in batched(part_of, self.batch_size):
            self.run(MERGE_PART_OF, rows=batch)
        if part_of:
            print(f"PART_OF ({len(part_of)})")

        related = payload["edges"].get("RELATED_TO", [])
        for batch in batched(related, self.batch_size):
            self.run(MERGE_RELATED_TO, rows=batch)
        if related:
            print(f"RELATED_TO ({len(related)})")

    # ---------------------------------------------------------------- verify
    def verify(self, manifest: dict[str, Any], settings: Settings) -> list[str]:
        """Compare the target against the manifest. Returns a list of problems."""
        problems: list[str] = []

        counts = self.run(
            "MATCH (v:Verse) RETURN count(v) AS verses, count(v.embedding) AS embedded"
        )[0]
        if counts["verses"] != manifest["verses"]:
            problems.append(
                f"verses: target has {counts['verses']}, manifest says {manifest['verses']}"
            )
        if counts["embedded"] != manifest["verses_with_embedding"]:
            problems.append(
                f"embedded verses: target has {counts['embedded']}, manifest says "
                f"{manifest['verses_with_embedding']} - unembedded verses are invisible "
                "to the vector channel"
            )

        for rel_type, expected in manifest["relationships"].items():
            actual = self.run(
                f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS n"
            )[0]["n"]
            if actual != expected:
                problems.append(f"{rel_type}: target has {actual}, manifest says {expected}")

        indexes = {
            row["name"]: row
            for row in self.run("SHOW INDEXES YIELD name, type, state RETURN name, type, state")
        }
        for wanted in (settings.neo4j_vector_index, settings.neo4j_fulltext_index):
            row = indexes.get(wanted)
            if row is None:
                problems.append(f"index {wanted!r} does not exist")
            elif row["state"] != "ONLINE":
                problems.append(
                    f"index {wanted!r} is {row['state']}, not ONLINE - a POPULATING "
                    "vector index returns poor neighbours rather than an error"
                )
        return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="data/graph_export", help="export directory")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check the target against the manifest without writing",
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    manifest_path = source / "manifest.json"
    if not manifest_path.exists():
        print(
            f"[FAIL] no manifest.json in {source}. Run: python -m scripts.export_neo4j "
            f"--out {source}",
            file=sys.stderr,
        )
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    settings: Settings = get_settings()

    # Before credentials, before any connection: the one mismatch the count
    # checks cannot see. Fails the same way with or without --verify-only.
    problem = manifest_precision_problem(manifest, settings)
    if problem:
        print(f"[FAIL] {problem}", file=sys.stderr)
        return 2
    if not manifest.get("embedder_precision_verified", False):
        print(
            "[WARN] the export did not verify its encoder against the graph "
            "(--skip-precision-check): embedder_precision is what the exporter was "
            "configured with, not what it measured. Fine for a synthetic graph; not "
            "for production.",
            file=sys.stderr,
        )

    if not settings.neo4j_password:
        print(
            "[FAIL] GITAGUIDE_NEO4J_PASSWORD is not set. Set the four connection "
            "variables in your environment; this script never prompts for them.",
            file=sys.stderr,
        )
        return 2

    if manifest["embedding_dim"] != settings.embedding_dim:
        print(
            f"[FAIL] the export was built with {manifest['embedding_dim']}-dimension "
            f"embeddings but this environment is configured for "
            f"{settings.embedding_dim}. The vector index would not match the data.",
            file=sys.stderr,
        )
        return 2

    from neo4j import GraphDatabase

    # Host, user and database are printed so the operator can see which target
    # they are about to write to. The password never is.
    print("GitaGuide graph import")
    print(f"  target   : {settings.neo4j_uri}")
    print(f"  user     : {settings.neo4j_username}")
    print(f"  database : {settings.neo4j_database}")
    print(f"  source   : {source.resolve()}")
    print(
        f"  exported : {manifest.get('exported_at', 'unknown')}, vectors from the "
        f"{manifest['embedder_precision']} encoder"
    )
    print(
        f"  expecting: {manifest['verses']} verses, "
        f"{manifest['relationships_total']} relationships\n"
    )

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    started = time.perf_counter()
    try:
        driver.verify_connectivity()
        importer = Importer(driver, settings.neo4j_database, args.batch_size)

        if not args.verify_only:
            importer.apply_schema(settings)
            importer.load_verses(load_verses(source / "verses.jsonl"))
            importer.load_relationships(
                json.loads((source / "relationships.json").read_text(encoding="utf-8"))
            )

        print("\nverifying against manifest.json")
        problems = importer.verify(manifest, settings)
        if problems:
            for problem in problems:
                print(f"  [FAIL] {problem}", file=sys.stderr)
            raise ImportVerificationError(
                f"{len(problems)} mismatch(es) between the target graph and the "
                "manifest. The import is incomplete; re-running is safe (every write "
                "is a MERGE on a stable key)."
            )

        print(f"  [ok] {manifest['verses']} verses, all embedded")
        for rel_type, expected in sorted(manifest["relationships"].items()):
            print(f"  [ok] {rel_type}: {expected}")
        print("  [ok] both indexes ONLINE")
        print(f"\nimport verified in {time.perf_counter() - started:.1f}s")
    except ImportVerificationError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
