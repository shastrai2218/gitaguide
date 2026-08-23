"""Diagnose the live Neo4j retrieval path, layer by layer.

Manual tool - never run by pytest. It answers, in order, the questions that a
``candidates_considered=0`` symptom actually raises:

1. can we connect at all?
2. are the vector and full-text indexes there, ONLINE, and the right dimension?
3. how many ``:Verse`` nodes carry an embedding?
4. does a *raw* ``db.index.vector.queryNodes`` return hits?
5. does ``HybridCypherRetriever.search()`` return items?
6. what **shape** are those items (dict vs repr string), and how many parse?

Step 6 is the one that was failing silently: the search worked and every record
was discarded by the parser. Any step that breaks prints the failing layer.

Usage
-----
::

    python -m scripts.diagnose_retrieval
    python -m scripts.diagnose_retrieval --query "my boss took credit" --emotions anger
    python -m scripts.diagnose_retrieval --top-k 10 --verbose

Needs ``GITAGUIDE_NEO4J_*`` in the environment or ``.env``. Credentials are
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import Any

from app.config import Settings, get_settings
from app.embeddings import build_embedder
from app.pipeline.retrieval import (
    Neo4jHybridRetriever,
    RetrievalError,
    _record_to_dict,
    _shape_of,
)
from app.pipeline.schemas import EmotionResult, EmotionScore

OK = "  [ok]  "
BAD = "  [FAIL]"
WARN = "  [warn]"


def step(index: int, title: str) -> None:
    print(f"\n{index}. {title}")


def diagnose(settings: Settings, query: str, emotions: list[str], top_k: int) -> int:
    from neo4j import GraphDatabase

    print("GitaGuide retrieval diagnosis")
    print(f"  uri      : {settings.neo4j_uri}")
    print(f"  database : {settings.neo4j_database}")
    print(f"  user     : {settings.neo4j_username}")
    print(f"  indexes  : vector={settings.neo4j_vector_index} "
          f"fulltext={settings.neo4j_fulltext_index}")
    print(f"  query    : {query!r}  emotions={emotions}")

    if not settings.neo4j_password:
        print(f"{BAD} no GITAGUIDE_NEO4J_PASSWORD set")
        return 2

    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )

    try:
        # ---- 1. connectivity ---------------------------------------------
        step(1, "connectivity")
        driver.verify_connectivity()
        print(f"{OK} connected")

        def run(cypher: str, **params: Any) -> list[dict[str, Any]]:
            records, _, _ = driver.execute_query(
                cypher, params, database_=settings.neo4j_database
            )
            return [record.data() for record in records]

        # ---- 2. indexes ---------------------------------------------------
        step(2, "indexes")
        indexes = run("SHOW INDEXES YIELD name, type, state, options RETURN *")
        by_name = {row["name"]: row for row in indexes}
        index_dimensions: int | None = None
        for wanted, kind in (
            (settings.neo4j_vector_index, "VECTOR"),
            (settings.neo4j_fulltext_index, "FULLTEXT"),
        ):
            row = by_name.get(wanted)
            if row is None:
                print(f"{BAD} index {wanted!r} does not exist "
                      f"(run: python -m kg.build_graph)")
                return 3
            state = row.get("state")
            marker = OK if state == "ONLINE" else WARN
            print(f"{marker} {wanted}: type={row.get('type')} state={state}")
            if kind == "VECTOR":
                config = (row.get("options") or {}).get("indexConfig") or {}
                raw_dimensions = config.get("vector.dimensions")
                index_dimensions = int(raw_dimensions) if raw_dimensions else None
                print(f"{OK} vector dimensions={index_dimensions}")

        # ---- 3. data ------------------------------------------------------
        step(3, "data")
        counts = run(
            "MATCH (v:Verse) "
            "RETURN count(v) AS verses, "
            "count(v.embedding) AS embedded, "
            "count { MATCH (:Verse)-[:ADDRESSES_EMOTION]->() } AS emotion_edges"
        )[0]
        print(f"{OK} verses={counts['verses']} embedded={counts['embedded']} "
              f"emotion_edges={counts['emotion_edges']}")
        if counts["embedded"] == 0:
            print(f"{BAD} no embeddings stored - the vector index cannot match")
            return 3

        # ---- 4. raw vector query ------------------------------------------
        step(4, "raw vector query")
        embedder = build_embedder(settings)
        vector = embedder.embed_one(query)
        print(f"{OK} embedder={embedder.name} dim={len(vector)}")
        if index_dimensions is not None and len(vector) != index_dimensions:
            print(f"{BAD} DIMENSION MISMATCH: embedder produces {len(vector)} but the "
                  f"index expects {index_dimensions}. Rebuild with the same embedder "
                  "(python -m kg.build_graph --reset) or fix GITAGUIDE_EMBEDDING_MODEL_ID.")
            return 3
        if embedder.name == "hashing-offline":
            print(f"{WARN} using the OFFLINE hashing embedder - it does not match the "
                  "vectors stored by a MiniLM build, so hits will look random. "
                  "Set GITAGUIDE_MOCK_MODE=false for a real comparison.")
        raw_hits = run(
            "CALL db.index.vector.queryNodes($index, $k, $vector) "
            "YIELD node, score RETURN node.id AS id, score",
            index=settings.neo4j_vector_index,
            k=top_k,
            vector=vector,
        )
        print(f"{OK} {len(raw_hits)} raw hit(s): "
              f"{[(h['id'], round(h['score'], 3)) for h in raw_hits[:5]]}")

        # ---- 5 + 6. the app path ------------------------------------------
        step(5, "app path: HybridCypherRetriever.search()")
        retriever = Neo4jHybridRetriever(settings, embedder=embedder, driver=driver)
        graphrag = retriever._get_retriever()
        result = graphrag.search(
            query_text=query,
            top_k=top_k,
            query_params={"emotions": emotions},
        )
        items = list(getattr(result, "items", None) or [])
        print(f"{OK} search returned {len(items)} item(s)")
        if items:
            first = items[0]
            print(f"{OK} item type   : {type(first).__name__}")
            print(f"{OK} content type: {type(getattr(first, 'content', None)).__name__}")
            print(f"{OK} shape       : {_shape_of(first)[:200]}")
            if isinstance(getattr(first, "content", None), str):
                print(f"{WARN} content is a STRING - result_formatter is not applied; "
                      "this is the silent-zero shape")

        step(6, "parsing")
        parsed = [record for item in items if (record := _record_to_dict(item)).get("id")]
        print(f"{OK} parsed {len(parsed)}/{len(items)} record(s)")
        if items and not parsed:
            print(f"{BAD} records returned but none parsed - the query and the parser disagree")
            return 4

        step(7, "full retrieve() with re-ranking")
        emotion = EmotionResult(
            labels=[EmotionScore(emotion=name, score=0.8) for name in emotions],
            intensity=0.6,
        ) if emotions else None
        trace = asyncio.run(retriever.retrieve(query, emotion=emotion))
        print(f"{OK} candidates={trace.candidates_considered} "
              f"parse_failures={trace.parse_failures} "
              f"results={len(trace.results)} latency={trace.latency_ms:.0f}ms")
        for item in trace.results:
            print(f"       {item.verse_id:12s} final={item.final_score:.3f} "
                  f"channels={'+'.join(item.retrieval_channels)}")
            if item.graph_paths:
                print(f"                    {item.graph_paths[0].description[:110]}")

        if trace.candidates_considered == 0 and raw_hits:
            print(f"\n{BAD} raw vector search finds hits but the app path yields none")
            return 4

        print("\nAll layers healthy.")
        return 0

    except RetrievalError as exc:
        print(f"\n{BAD} retrieval error: {exc}")
        return 4
    except Exception as exc:
        print(f"\n{BAD} {type(exc).__name__}: {exc}")
        return 1
    finally:
        driver.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose live Neo4j retrieval")
    parser.add_argument("--query", default="my manager took credit for my work")
    parser.add_argument("--emotions", nargs="*", default=["anger", "disappointment"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--verbose", action="store_true", help="show library debug logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(name)s :: %(message)s",
    )

    # Force the real backends regardless of MOCK_MODE: this tool exists to test
    # the live path.
    settings = get_settings().model_copy(
        update={"mock_mode": False, "use_real_neo4j": True}
    )
    return diagnose(settings, args.query, list(args.emotions), args.top_k)


if __name__ == "__main__":
    sys.exit(main())
