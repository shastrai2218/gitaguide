"""Load the corpus into Neo4j: nodes, edges, vector index, full-text index.

Research-pipeline role
---------------------
This is the one-off (idempotent) step that turns flat verse JSON into the
knowledge graph the retriever traverses:

* ``(:Scripture)`` and ``(:Verse)`` nodes, with a MiniLM embedding on each verse
* ``(:Theme)``, ``(:Emotion)``, ``(:Concept)`` nodes from the weak labels
* ``HAS_THEME`` / ``ADDRESSES_EMOTION`` / ``MENTIONS`` edges
* ``RELATED_TO`` edges between verses that share >= N themes/concepts
* a cosine **vector index** and a **full-text index**, the two halves that
  ``neo4j-graphrag``'s HybridCypherRetriever fuses

Offline mode (``--dry-run``, or simply no Neo4j credentials) writes the exact
same node/edge set to ``corpus/data/graph_dump.json`` instead, so the graph can
be inspected and diffed without a database.

Usage
-----
::

    python -m kg.build_graph --reset                 # build into Neo4j
    python -m kg.build_graph --dry-run               # offline JSON dump
    python -m kg.build_graph --corpus corpus/data/gita.json --limit 200
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import CORPUS_DATA_DIR, SAMPLE_CORPUS_PATH, Settings, get_settings
from app.embeddings import Embedder, build_embedder
from corpus.schemas import Corpus, ScriptureSource, Verse
from kg.cypher_queries import (
    CONSTRAINTS,
    CREATE_FULLTEXT_INDEX,
    CREATE_VECTOR_INDEX,
    DROP_ALL,
    GRAPH_STATS,
    LINK_RELATED_VERSES,
    MERGE_SCRIPTURE,
    MERGE_VERSE,
    format_index_ddl,
)

logger = logging.getLogger(__name__)

#: Index names come from settings; validate them before string substitution.
INDEX_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")

#: Two shared themes/concepts is enough to call two verses related.
DEFAULT_MIN_SHARED = 2


@dataclass
class BuildStats:
    """What the build actually wrote (printed at the end, kept for the report)."""

    verses: int = 0
    themes: int = 0
    emotions: int = 0
    concepts: int = 0
    related_edges: int = 0
    embedded: int = 0
    backend: str = "neo4j"
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "verses": self.verses,
            "themes": self.themes,
            "emotions": self.emotions,
            "concepts": self.concepts,
            "related_edges": self.related_edges,
            "embedded": self.embedded,
            **self.extra,
        }


def _verse_params(verse: Verse, embedding: Sequence[float]) -> dict[str, Any]:
    return {
        "id": verse.id,
        "source": verse.source.value,
        "chapter": verse.chapter,
        "verse": verse.verse,
        "book": verse.book,
        "citation": verse.citation,
        "text_english": verse.text_english,
        "text_sanskrit": verse.text_sanskrit,
        "transliteration": verse.transliteration,
        "commentary": verse.commentary,
        "embedding": list(embedding),
        "themes": verse.themes,
        "emotions": verse.emotions_addressed,
        "concepts": verse.concepts,
    }


def _validate_index_name(name: str) -> str:
    if not INDEX_NAME_RE.match(name):
        raise ValueError(f"unsafe index name {name!r}")
    return name


class GraphBuilder:
    """Writes a :class:`Corpus` into Neo4j (or to a JSON dump when dry-run)."""

    def __init__(
        self,
        settings: Settings | None = None,
        embedder: Embedder | None = None,
        dry_run: bool = False,
        driver: Any | None = None,
        allow_offline_embeddings: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or build_embedder(self.settings)
        self.dry_run = dry_run or not self.settings.neo4j_enabled
        self.allow_offline_embeddings = allow_offline_embeddings
        self._driver = driver

    # ------------------------------------------------------------- plumbing
    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password),
            )
            self._driver.verify_connectivity()
        return self._driver

    def _run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        driver = self._get_driver()
        with driver.session(database=self.settings.neo4j_database) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ---------------------------------------------------------------- build
    def build(
        self,
        corpus: Corpus,
        reset: bool = False,
        min_shared: int = DEFAULT_MIN_SHARED,
        dump_path: Path | None = None,
    ) -> BuildStats:
        """Create schema, write nodes/edges, then build both indexes."""
        self._check_embedder_is_safe()
        embeddings = self._embed(corpus)

        if self.dry_run:
            return self._dump(corpus, embeddings, min_shared, dump_path)

        if reset:
            logger.warning("wiping the database (--reset)")
            self._run(DROP_ALL)

        for statement in CONSTRAINTS:
            self._run(statement)

        for source in ScriptureSource:
            self._run(MERGE_SCRIPTURE, key=source.value, name=source.display_name)

        for verse in corpus:
            self._run(MERGE_VERSE, **_verse_params(verse, embeddings[verse.id]))

        related = self._run(LINK_RELATED_VERSES, min_shared=min_shared)
        related_count = int(related[0]["created"]) if related else 0

        self._create_indexes()
        stats_rows = self._run(GRAPH_STATS)
        row = stats_rows[0] if stats_rows else {}

        stats = BuildStats(
            verses=int(row.get("verses", len(corpus))),
            themes=int(row.get("themes", 0)),
            emotions=int(row.get("emotions", 0)),
            # Was hardcoded to 0, which made every real build report
            # "concepts: 0" no matter how many Concept nodes it had just
            # written. Only the --dry-run branch counted them, so the bug hid
            # behind sample-corpus runs.
            concepts=int(row.get("concepts", 0)),
            related_edges=int(row.get("related_edges", related_count)),
            embedded=len(embeddings),
            backend="neo4j",
            extra={
                "uri": self.settings.neo4j_uri,
                "database": self.settings.neo4j_database,
                "verses_with_book": int(row.get("verses_with_book", 0)),
            },
        )
        logger.info("graph build complete: %s", stats.as_dict())
        return stats

    def _check_embedder_is_safe(self) -> None:
        """Refuse to write offline vectors into a real vector index.

        ``GITAGUIDE_MOCK_MODE=true`` selects the deterministic hashing embedder.
        Writing those vectors into a database whose index was built with MiniLM
        does not fail - it silently poisons the index with two incompatible
        embedding spaces, and retrieval quietly degrades to noise. Fail loud
        instead; ``--allow-offline-embeddings`` is the deliberate override.
        """
        if self.dry_run or self.allow_offline_embeddings:
            return
        if self.embedder.name == "hashing-offline":
            raise ValueError(
                "refusing to write hashing-offline embeddings into Neo4j: they are "
                "not comparable with MiniLM vectors, and mixing the two silently "
                "breaks vector search. Set GITAGUIDE_MOCK_MODE=false to use "
                f"{self.settings.embedding_model_id}, or pass "
                "--allow-offline-embeddings if you really mean it."
            )
        logger.info("embedding with %s (dim=%s)", self.embedder.name, self.embedder.dim)

    def _create_indexes(self) -> None:
        vector_name = _validate_index_name(self.settings.neo4j_vector_index)
        fulltext_name = _validate_index_name(self.settings.neo4j_fulltext_index)
        dimensions = self.embedder.dim or self.settings.embedding_dim

        self._run(format_index_ddl(CREATE_VECTOR_INDEX, vector_name, dimensions))
        self._run(format_index_ddl(CREATE_FULLTEXT_INDEX, fulltext_name))
        logger.info(
            "indexes ensured: vector=%s (dim=%d), fulltext=%s",
            vector_name,
            dimensions,
            fulltext_name,
        )

    # ------------------------------------------------------------ embedding
    def _embed(self, corpus: Corpus) -> dict[str, list[float]]:
        verses = list(corpus)
        if not verses:
            return {}
        logger.info("embedding %d verses with %s", len(verses), self.embedder.name)
        vectors = self.embedder.embed([verse.embedding_text() for verse in verses])
        return {verse.id: vector for verse, vector in zip(verses, vectors, strict=True)}

    # ----------------------------------------------------------- offline dump
    def _dump(
        self,
        corpus: Corpus,
        embeddings: dict[str, list[float]],
        min_shared: int,
        dump_path: Path | None,
    ) -> BuildStats:
        """Materialise the identical node/edge set as JSON (offline mode)."""
        themes: set[str] = set()
        emotions: set[str] = set()
        concepts: set[str] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for source in ScriptureSource:
            nodes.append(
                {"label": "Scripture", "key": source.value, "name": source.display_name}
            )

        for verse in corpus:
            nodes.append(
                {
                    "label": "Verse",
                    "id": verse.id,
                    "source": verse.source.value,
                    "citation": verse.citation,
                    "text_english": verse.text_english,
                    "embedding_dim": len(embeddings.get(verse.id, [])),
                }
            )
            edges.append({"type": "PART_OF", "from": verse.id, "to": verse.source.value})
            for theme in verse.themes:
                themes.add(theme)
                edges.append({"type": "HAS_THEME", "from": verse.id, "to": theme})
            for emotion in verse.emotions_addressed:
                emotions.add(emotion)
                edges.append(
                    {
                        "type": "ADDRESSES_EMOTION",
                        "from": verse.id,
                        "to": emotion,
                        "weight": 1.0,
                    }
                )
            for concept in verse.concepts:
                concepts.add(concept)
                edges.append({"type": "MENTIONS", "from": verse.id, "to": concept})

        nodes += [{"label": "Theme", "name": name} for name in sorted(themes)]
        nodes += [{"label": "Emotion", "name": name} for name in sorted(emotions)]
        nodes += [{"label": "Concept", "name": name} for name in sorted(concepts)]

        related = list(_related_pairs(corpus, min_shared))
        edges += [
            {"type": "RELATED_TO", "from": left, "to": right, "shared": shared}
            for left, right, shared in related
        ]

        target = dump_path or (CORPUS_DATA_DIR / "graph_dump.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        stats = BuildStats(
            verses=len(corpus),
            themes=len(themes),
            emotions=len(emotions),
            concepts=len(concepts),
            related_edges=len(related),
            embedded=len(embeddings),
            backend="dry-run",
            extra={"dump": str(target), "nodes": len(nodes), "edges": len(edges)},
        )
        logger.info("dry-run graph written: %s", stats.as_dict())
        return stats


def _related_pairs(corpus: Corpus, min_shared: int) -> Iterable[tuple[str, str, int]]:
    """Verse pairs sharing at least ``min_shared`` themes/concepts."""
    labelled = [
        (verse.id, set(verse.themes) | set(verse.concepts)) for verse in corpus
    ]
    for index, (left_id, left_labels) in enumerate(labelled):
        for right_id, right_labels in labelled[index + 1 :]:
            shared = len(left_labels & right_labels)
            if shared >= min_shared:
                first, second = sorted((left_id, right_id))
                yield first, second, shared


def load_corpus_files(paths: Sequence[str]) -> Corpus:
    """Merge one or more corpus JSON files (falls back to the bundled sample)."""
    corpus = Corpus()
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            corpus.extend(Corpus.load(path))
        else:
            logger.warning("corpus file %s not found; skipping", path)
    if not len(corpus):
        logger.info("no corpus files loaded; using the bundled sample corpus")
        corpus = Corpus.load(SAMPLE_CORPUS_PATH)
    return corpus


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the GitaGuide knowledge graph")
    parser.add_argument(
        "--corpus",
        nargs="*",
        default=[
            str(CORPUS_DATA_DIR / "gita.json"),
            str(CORPUS_DATA_DIR / "arthashastra.json"),
        ],
        help="corpus JSON files to load",
    )
    parser.add_argument("--reset", action="store_true", help="delete all nodes first")
    parser.add_argument("--dry-run", action="store_true", help="write a JSON dump instead")
    parser.add_argument(
        "--allow-offline-embeddings",
        action="store_true",
        help="permit writing hashing-offline vectors into Neo4j (normally refused)",
    )
    parser.add_argument("--min-shared", type=int, default=DEFAULT_MIN_SHARED)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dump", default=None, help="path for the dry-run JSON dump")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    corpus = load_corpus_files(args.corpus)
    if args.limit:
        corpus = Corpus(verses=list(corpus)[: args.limit])

    builder = GraphBuilder(
        dry_run=args.dry_run,
        allow_offline_embeddings=args.allow_offline_embeddings,
    )
    try:
        stats = builder.build(
            corpus,
            reset=args.reset,
            min_shared=args.min_shared,
            dump_path=Path(args.dump) if args.dump else None,
        )
    finally:
        builder.close()

    print(json.dumps(stats.as_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
