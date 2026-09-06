"""Rewrite every verse embedding in a graph with the configured encoder.

Why this exists
---------------
The index and the query encoder must be the *same* encoder. Vectors are compared
by cosine, so a graph built with one and queried with another does not error - it
quietly returns worse neighbours, which looks exactly like a bad reranker. When
``GITAGUIDE_EMBEDDER_PRECISION`` changes, the stored vectors have to change with
it, and this is the tool that does it.

It rewrites **only** ``v.embedding``. Tags, labels, ``RELATED_TO`` edges and the
hub-suppression pass are untouched, so the graph stays the artefact every figure
in ``docs/kg_measurements.md`` was measured against, with one property replaced.
A full rebuild would re-run the tagger and change far more than precision.

The text embedded is ``Verse.embedding_text()`` read from the corpus files - the
same composition ``kg.build_graph`` uses - rather than anything reconstructed
from graph properties, so the only difference from the original build is the
encoder.

Usage
-----
::

    # local: re-embed with whatever GITAGUIDE_EMBEDDER_PRECISION says
    GITAGUIDE_EMBEDDER_PRECISION=int8 python -m scripts.reembed_graph

    python -m scripts.reembed_graph --dry-run     # report, write nothing

Connection details come from ``GITAGUIDE_NEO4J_*``. Credentials are never
printed. After this, re-export and re-import to move the new vectors to Aura -
see docs/DATA_MIGRATION.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.embeddings import build_embedder
from corpus.schemas import Corpus

CORPUS_DATA_DIR = Path(__file__).resolve().parent.parent / "corpus" / "data"

#: One statement per batch of verses. Each carries a 384-float vector, so this
#: keeps a batch around 1.5 MB.
DEFAULT_BATCH_SIZE = 500

UPDATE_EMBEDDINGS = """
UNWIND $rows AS row
MATCH (v:Verse {id: row.id})
SET v.embedding = row.embedding
RETURN count(v) AS updated
"""


class ReembedError(RuntimeError):
    """The graph and the corpus disagree, or the encoder is unusable."""


def load_corpus(paths: list[str]) -> Corpus:
    corpus = Corpus()
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise ReembedError(f"corpus file not found: {path}")
        corpus.extend(Corpus.load(path))
    if not len(corpus):
        raise ReembedError("the corpus files loaded zero verses")
    return corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--corpus",
        nargs="+",
        default=[
            str(CORPUS_DATA_DIR / "gita.json"),
            str(CORPUS_DATA_DIR / "arthashastra.json"),
        ],
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    if settings.mock_mode:
        print(
            "[FAIL] mock mode selects the hashing embedder, whose vectors are "
            "unrelated to MiniLM's. Refusing to write them into a graph.",
            file=sys.stderr,
        )
        return 2
    if not settings.neo4j_password:
        print("[FAIL] no GITAGUIDE_NEO4J_PASSWORD set", file=sys.stderr)
        return 2

    corpus = load_corpus(args.corpus)
    verses = list(corpus)
    embedder = build_embedder(settings)

    print("re-embedding graph verses")
    print(f"  target    : {settings.neo4j_uri} (database={settings.neo4j_database})")
    print(f"  encoder   : {embedder.name}")
    print(f"  precision : {settings.embedder_precision}")
    print(f"  corpus    : {len(verses)} verses")

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    started = time.perf_counter()
    try:
        driver.verify_connectivity()

        def run(cypher: str, **params: Any) -> list[dict[str, Any]]:
            records, _, _ = driver.execute_query(
                cypher, params, database_=settings.neo4j_database
            )
            return [record.data() for record in records]

        in_graph = {
            row["id"]
            for row in run("MATCH (v:Verse) RETURN v.id AS id")
        }
        corpus_ids = {verse.id for verse in verses}

        # A mismatch means the graph was not built from this corpus. Writing
        # anyway would leave some verses on the old encoder and some on the
        # new one - a mixed vector space, which is the exact failure this
        # script exists to prevent.
        missing_in_graph = corpus_ids - in_graph
        missing_in_corpus = in_graph - corpus_ids
        if missing_in_graph or missing_in_corpus:
            raise ReembedError(
                f"corpus and graph disagree: {len(missing_in_graph)} verse(s) in "
                f"the corpus are not in the graph, {len(missing_in_corpus)} in the "
                "graph are not in the corpus. Re-embedding would leave a mixed "
                "vector space. Rebuild the graph first (python -m kg.build_graph)."
            )
        print(f"  graph     : {len(in_graph)} verses, ids match the corpus")

        vectors = embedder.embed([verse.embedding_text() for verse in verses])
        print(f"  embedded  : {len(vectors)} vectors, dim={len(vectors[0])}")
        if len(vectors[0]) != settings.embedding_dim:
            raise ReembedError(
                f"encoder produced {len(vectors[0])}-dimension vectors but the "
                f"index expects {settings.embedding_dim}"
            )

        if args.dry_run:
            print("\n--dry-run: nothing written")
            return 0

        rows = [
            {"id": verse.id, "embedding": list(vector)}
            for verse, vector in zip(verses, vectors, strict=True)
        ]
        updated = 0
        for start in range(0, len(rows), args.batch_size):
            batch = rows[start : start + args.batch_size]
            updated += run(UPDATE_EMBEDDINGS, rows=batch)[0]["updated"]
            print(f"  written   : {updated}/{len(rows)}", end="\r", flush=True)
        print(f"  written   : {updated}/{len(rows)}          ")

        if updated != len(rows):
            raise ReembedError(
                f"only {updated} of {len(rows)} verses were updated; the graph now "
                "holds a mix of old and new vectors. Re-run this script."
            )

        still_embedded = run(
            "MATCH (v:Verse) RETURN count(v) AS total, count(v.embedding) AS embedded"
        )[0]
        if still_embedded["embedded"] != still_embedded["total"]:
            raise ReembedError(
                f"{still_embedded['total'] - still_embedded['embedded']} verse(s) "
                "have no embedding after the rewrite"
            )

        print(f"\nall {updated} verses re-embedded with {embedder.name} "
              f"in {time.perf_counter() - started:.1f}s")
        print("next: re-run the retrieval eval, then re-export and re-import to Aura")
    except ReembedError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
