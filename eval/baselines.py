"""Retrieval baselines and ablations over the exported production graph.

Research-pipeline role
---------------------
``eval/retrieval_eval.py`` measures the live ``neo4j-hybrid-cypher`` retriever,
which needs Aura credentials. This harness answers the questions a reviewer asks
of that single number - *compared with what?* and *what does the graph add?* -
without them, by replaying the production retrieval path over
``data/graph_export`` (the same 3,836 verses, the same stored INT8 vectors and
the same 14,709 relationships that were imported into Aura):

* vector leg   - exact cosine over the stored vectors, reported on Neo4j's
  ``(1 + cos) / 2`` scale, top ``retrieval_candidate_k``;
* full-text leg - Lucene-style BM25 (k1 = 1.2, b = 0.75) per field over
  ``text_english`` and ``transliteration`` (``commentary`` is empty in the
  export), with the query-parser behaviour that matters here: a ``name:``
  token turns the next term into a query on a field that does not exist;
* fusion        - the package's naive ranker (each leg divided by its own
  maximum, per-node maximum), exactly as :data:`kg.cypher_queries.MANUAL_HYBRID_QUERY`;
* tail          - the ``HYBRID_RETRIEVAL_QUERY`` graph annotations and
  :func:`app.pipeline.retrieval.rerank`, unchanged.

It is a *replica*, not the live system: Aura's HNSW is approximate where this is
exact, and Lucene's lossy length norms are not reproduced. The first row it
prints is therefore the production configuration, to be checked against the
live result (hit 0.175, MRR 0.074 on 7 Sep 2026, deterministic intent, INT8)
before any other row is read.

Every configuration runs with deterministic intent: the live intent classifier
makes identical runs differ by up to 60% relative (kg_measurements.md s.12).

Usage
-----
::

    GITAGUIDE_MOCK_MODE=false python -m eval.baselines
    GITAGUIDE_MOCK_MODE=false python -m eval.baselines --nemotron   # needs NVIDIA_API_KEY
    ... --json results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from app.config import PROJECT_ROOT, Settings, get_settings
from app.pipeline.emotion import EmotionDetector
from app.pipeline.intent import IntentClassifier
from app.pipeline.retrieval import expand_query, scripture_affinity
from app.pipeline.schemas import EmotionResult, IntentResult
from corpus.schemas import ScriptureSource
from eval.retrieval_eval import GOLD_PATH, evaluate_one, git_commit, load_gold

EXPORT_DIR = PROJECT_ROOT / "data" / "graph_export"
NEMOTRON_MODEL = "nvidia/nemotron-3-embed-1b"
NIM_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"

VECTOR_WEIGHT = 0.55
FULLTEXT_WEIGHT = 0.20


# --------------------------------------------------------------------- graph
@dataclass
class ExportedGraph:
    ids: list[str]
    source: dict[str, ScriptureSource]
    text: dict[str, str]
    translit: dict[str, str]
    vectors: np.ndarray  # rows follow ``ids``, L2-normalised
    emotions: dict[str, set[str]]
    themes: dict[str, set[str]]
    related: dict[str, set[str]]

    def embedding_text(self, verse_id: str) -> str:
        """Mirror of :meth:`corpus.schemas.Verse.embedding_text`."""
        parts = [self.text[verse_id].strip()]
        if self.themes[verse_id]:
            parts.append("Themes: " + ", ".join(sorted(self.themes[verse_id])))
        if self.emotions[verse_id]:
            parts.append("Addresses: " + ", ".join(sorted(self.emotions[verse_id])))
        return "\n".join(parts)


def load_export(path: Path = EXPORT_DIR) -> ExportedGraph:
    ids: list[str] = []
    source: dict[str, ScriptureSource] = {}
    text: dict[str, str] = {}
    translit: dict[str, str] = {}
    rows: list[list[float]] = []
    with (path / "verses.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            ids.append(record["id"])
            source[record["id"]] = ScriptureSource(record["source"])
            text[record["id"]] = record.get("text_english") or ""
            translit[record["id"]] = record.get("transliteration") or ""
            rows.append(record["embedding"])
    vectors = np.asarray(rows, dtype=np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    emotions: dict[str, set[str]] = defaultdict(set)
    themes: dict[str, set[str]] = defaultdict(set)
    related: dict[str, set[str]] = defaultdict(set)
    edges = json.loads((path / "relationships.json").read_text(encoding="utf-8"))["edges"]
    for edge in edges.get("ADDRESSES_EMOTION", []):
        emotions[edge["verse_id"]].add(edge["name"])
    for edge in edges.get("HAS_THEME", []):
        themes[edge["verse_id"]].add(edge["name"])
    for edge in edges.get("RELATED_TO", []):
        a, b = _edge_ends(edge)
        related[a].add(b)
        related[b].add(a)  # the tail matches RELATED_TO undirected
    return ExportedGraph(ids, source, text, translit, vectors, emotions, themes, related)


def _edge_ends(edge: dict[str, Any]) -> tuple[str, str]:
    for left, right in (("from_id", "to_id"), ("from", "to"), ("source", "target")):
        if left in edge and right in edge:
            return str(edge[left]), str(edge[right])
    keys = [key for key in edge if key.endswith("id")]
    if len(keys) >= 2:
        return str(edge[keys[0]]), str(edge[keys[1]])
    raise KeyError(f"cannot read RELATED_TO endpoints from {sorted(edge)}")


# ------------------------------------------------------------------ full-text
_LUCENE_TOKEN = re.compile(r"\w+(?:'\w+)*", re.UNICODE)
_FIELD_CLAUSE = re.compile(r"\b\w+:\s*[^\s|,]+")
_WILDCARD = re.compile(r"\S*[?*]\S*")


def lucene_terms(text: str) -> list[str]:
    """StandardAnalyzer without a stop list: Unicode words, lower-cased."""
    return [token.lower() for token in _LUCENE_TOKEN.findall(text)]


def parse_query(query: str) -> list[str]:
    """The terms Lucene's classic parser leaves on the default fields.

    ``Felt emotions: grief`` parses as a clause on a field called ``emotions``
    and ``Intent: seek guidance`` as one on ``Intent``; neither field exists, so
    both clauses match nothing. A token containing ``?`` or ``*`` is a wildcard
    query (``point?``), which matches no real term in practice.
    """
    query = _FIELD_CLAUSE.sub(" ", query)
    query = _WILDCARD.sub(" ", query)
    return lucene_terms(query)


class BM25Field:
    def __init__(self, docs: dict[str, str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tf: dict[str, dict[str, int]] = {}
        self.length: dict[str, int] = {}
        df: dict[str, int] = defaultdict(int)
        for verse_id, value in docs.items():
            terms = lucene_terms(value)
            if not terms:
                continue
            counts: dict[str, int] = defaultdict(int)
            for term in terms:
                counts[term] += 1
            self.tf[verse_id] = counts
            self.length[verse_id] = len(terms)
            for term in counts:
                df[term] += 1
        n = max(1, len(self.tf))
        self.avg = sum(self.length.values()) / n if self.length else 1.0
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, terms: Sequence[str]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for term in terms:  # repeated query terms count again, as SHOULD clauses do
            idf = self.idf.get(term)
            if idf is None:
                continue
            for verse_id, counts in self.tf.items():
                tf = counts.get(term)
                if not tf:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self.length[verse_id] / self.avg)
                out[verse_id] += idf * tf / (tf + norm)
        return out


class FullText:
    def __init__(self, graph: ExportedGraph) -> None:
        self.fields = [BM25Field(graph.text), BM25Field(graph.translit)]

    def scores(self, query: str) -> dict[str, float]:
        terms = parse_query(query)
        total: dict[str, float] = defaultdict(float)
        for index in self.fields:
            for verse_id, score in index.scores(terms).items():
                total[verse_id] += score
        return dict(total)


# ------------------------------------------------------------------ encoders
class QueryEncoder:
    name = "encoder"

    def encode(self, text: str) -> np.ndarray:
        raise NotImplementedError


class MiniLMQueryEncoder(QueryEncoder):
    """The serving encoder (INT8 ONNX, batch of one), as production embeds a query."""

    def __init__(self, settings: Settings) -> None:
        from app.embeddings import build_embedder

        self.embedder = build_embedder(settings)
        self.name = self.embedder.name

    def encode(self, text: str) -> np.ndarray:
        vector = np.asarray(self.embedder.embed_one(text), dtype=np.float64)
        return vector / np.linalg.norm(vector)

    def passages(self, texts: list[str], cache: Path) -> np.ndarray:
        """Re-embed an index one text at a time (INT8 is batch-sensitive)."""
        return _cached(cache, len(texts), lambda: [self.encode(text) for text in texts])


def _cached(cache: Path, rows: int, build: Callable[[], Sequence[Any]]) -> np.ndarray:
    if cache.exists():
        matrix = np.load(cache)
        if matrix.shape[0] == rows:
            return matrix
    matrix = np.asarray(build(), dtype=np.float64)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, matrix)
    return matrix


def passage_texts(graph: ExportedGraph, tagged: bool) -> list[str]:
    """Index text: production's (verse + weak-label lines) or the verse alone."""
    if tagged:
        return [graph.embedding_text(verse_id) for verse_id in graph.ids]
    return [graph.text[verse_id] for verse_id in graph.ids]


class NemotronEncoder(QueryEncoder):
    """``nvidia/nemotron-3-embed-1b`` over NIM; passages cached on disk."""

    name = NEMOTRON_MODEL

    def __init__(self, api_key: str) -> None:
        import httpx

        self.client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {api_key}"})

    def _call(self, texts: list[str], input_type: str) -> list[list[float]]:
        body = {
            "model": NEMOTRON_MODEL,
            "input": texts,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        }
        for attempt in range(6):
            response = self.client.post(NIM_EMBED_URL, json=body)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in data]
        response.raise_for_status()
        raise RuntimeError("unreachable")

    def encode(self, text: str) -> np.ndarray:
        vector = np.asarray(self._call([text], "query")[0], dtype=np.float64)
        return vector / np.linalg.norm(vector)

    def passages(self, texts: list[str], cache: Path, batch: int = 32) -> np.ndarray:
        def build() -> list[list[float]]:
            rows: list[list[float]] = []
            for start in range(0, len(texts), batch):
                rows.extend(self._call(texts[start : start + batch], "passage"))
            return rows

        return _cached(cache, len(texts), build)


# ----------------------------------------------------------------- retriever
@dataclass
class Config:
    name: str
    vector: bool = True
    fulltext: bool = True
    expand: bool = True  # emotion/intent terms appended, as production does
    rerank: bool = True  # 0.75 * fused + graph + intent prior
    graph_weight: float = 0.15
    intent_weight: float = 0.25
    graph_candidates: int = 0  # >0: the graph also *retrieves* (emotion-edge pool)
    encoder: str = "minilm"
    note: str = ""


@dataclass
class Replica:
    graph: ExportedGraph
    fulltext: FullText
    encoders: dict[str, tuple[QueryEncoder, np.ndarray]]
    settings: Settings
    index: dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        self.index = {verse_id: i for i, verse_id in enumerate(self.graph.ids)}

    def _vector_leg(self, cfg: Config, text: str, k: int) -> tuple[dict[str, float], np.ndarray]:
        encoder, matrix = self.encoders[cfg.encoder]
        cosine = matrix @ encoder.encode(text)
        neo4j = (1.0 + cosine) / 2.0  # Neo4j's cosine score scale
        top = np.argsort(-neo4j, kind="stable")[:k]
        return {self.graph.ids[i]: float(neo4j[i]) for i in top}, neo4j

    def retrieve(
        self, cfg: Config, query: str, emotion: EmotionResult, intent: IntentResult, top_k: int
    ) -> list[str]:
        k = self.settings.retrieval_candidate_k
        text = expand_query(query, emotion, intent) if cfg.expand else query

        legs: list[dict[str, float]] = []
        all_vector: np.ndarray | None = None
        if cfg.vector:
            hits, all_vector = self._vector_leg(cfg, text, k)
            legs.append(hits)
        if cfg.fulltext:
            raw = self.fulltext.scores(text)
            top = sorted(raw.items(), key=lambda item: (-item[1], item[0]))[:k]
            legs.append(dict(top))

        fused: dict[str, float] = {}
        for leg in legs:
            best = max(leg.values(), default=0.0)
            for verse_id, score in leg.items():
                value = score / best if best > 0 else 0.0
                fused[verse_id] = max(fused.get(verse_id, 0.0), value)
        pool = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:k]
        search = dict(pool)

        emotions = emotion.top_emotions(self.settings.emotion_top_k)
        if cfg.graph_candidates and all_vector is not None:
            # Graph as a retriever: verses on an edge to a detected emotion enter
            # the pool on their own vector score, whether or not a leg found them.
            reachable = {
                verse_id
                for verse_id, tags in self.graph.emotions.items()
                if tags & set(emotions[:3])
            } - set(search)
            ranked = sorted(reachable, key=lambda verse_id: -all_vector[self.index[verse_id]])[
                : cfg.graph_candidates
            ]
            best = max(search.values(), default=1.0)
            vec_best = float(max(all_vector)) or 1.0
            for verse_id in ranked:
                search[verse_id] = float(all_vector[self.index[verse_id]]) / vec_best * best

        if not cfg.rerank:
            ranked_ids = sorted(search, key=lambda verse_id: (-search[verse_id], verse_id))
            return ranked_ids[:top_k]

        wanted = set(emotions)
        scored: list[tuple[float, str]] = []
        for verse_id, score in search.items():
            matched = self.graph.emotions[verse_id] & wanted
            siblings = [
                (sibling, via)
                for sibling in sorted(self.graph.related[verse_id])
                for via in sorted(self.graph.emotions[sibling] & wanted)
            ][:3]
            paths = len(matched) + len(siblings)
            graph_score = min(1.0, 0.5 * paths) if paths else 0.0
            boost = cfg.intent_weight * scripture_affinity(
                intent.intent, self.graph.source[verse_id]
            )
            final = (
                (VECTOR_WEIGHT + FULLTEXT_WEIGHT) * min(1.0, score)
                + cfg.graph_weight * graph_score
                + boost
            )
            scored.append((round(final, 6), verse_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [verse_id for _, verse_id in scored[:top_k]]


# ------------------------------------------------------------------ statistics
def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(a_only: int, b_only: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs."""
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_bootstrap(
    left: Sequence[float], right: Sequence[float], rounds: int = 10000, seed: int = 7
) -> tuple[float, float, float]:
    """Mean difference (right - left) and its 95% percentile interval."""
    rng = random.Random(seed)
    diffs = [b - a for a, b in zip(left, right, strict=True)]
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    return (sum(diffs) / n, means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1])


# ------------------------------------------------------------------------ run
def configurations(nemotron: bool) -> list[Config]:
    configs = [
        Config("production replica", note="calibration row"),
        Config("BM25 only, raw query", vector=False, expand=False, rerank=False),
        Config("BM25 only", vector=False, rerank=False),
        Config("vector only, raw query", fulltext=False, expand=False, rerank=False),
        Config("vector only", fulltext=False, rerank=False),
        Config("fusion (vector + BM25)", rerank=False),
        Config("fusion + intent prior", graph_weight=0.0),
        Config("fusion + graph", intent_weight=0.0),
        Config("graph weight 0.30", graph_weight=0.30),
        Config("graph weight 0.50", graph_weight=0.50),
        Config("graph retrieves (+20 via emotion edges)", graph_candidates=20),
        Config("production, raw query", expand=False),
        Config(
            "untagged index: vector only, raw query",
            fulltext=False,
            expand=False,
            rerank=False,
            encoder="minilm-plain",
        ),
        Config("untagged index: production pipeline", encoder="minilm-plain"),
        Config("untagged index: pipeline, raw query", expand=False, encoder="minilm-plain"),
    ]
    if nemotron:
        for encoder, label in (("nemotron", "Nemotron"), ("nemotron-plain", "Nemotron untagged")):
            configs += [
                Config(
                    f"{label}: vector only, raw query",
                    fulltext=False,
                    expand=False,
                    rerank=False,
                    encoder=encoder,
                ),
                Config(f"{label}: vector only", fulltext=False, rerank=False, encoder=encoder),
                Config(f"{label}: production pipeline", encoder=encoder),
                Config(f"{label}: pipeline, raw query", expand=False, encoder=encoder),
            ]
    return configs


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings().model_copy()
    graph = load_export()
    gold = load_gold(Path(args.gold))
    # A query no pooled system answered has no relevant verse; no retriever
    # can score on it, so it is counted and reported rather than scored as a miss.
    pairs = [p for p in gold["pairs"] if p.get("relevant")]
    unanswerable = [p["id"] for p in gold["pairs"] if not p.get("relevant")]

    minilm = MiniLMQueryEncoder(settings)
    # Sanity: the stored vectors must be what the serving encoder produces from
    # this harness's mirror of Verse.embedding_text. Tagged verses are sampled
    # on purpose - untagged ones would pass even if the mirror were wrong.
    tagged = [i for i, v in enumerate(graph.ids) if graph.themes[v] or graph.emotions[v]]
    sample = tagged[:: max(1, len(tagged) // 20)][:20]
    parity = min(
        float(graph.vectors[i] @ minilm.encode(graph.embedding_text(graph.ids[i]))) for i in sample
    )
    cache = Path(args.cache)
    encoders: dict[str, tuple[QueryEncoder, np.ndarray]] = {
        "minilm": (minilm, graph.vectors),
        "minilm-plain": (
            minilm,
            minilm.passages(passage_texts(graph, tagged=False), cache / "minilm_int8_plain.npy"),
        ),
    }

    if args.nemotron:
        key = os.environ.get("NVIDIA_API_KEY") or _key_from(PROJECT_ROOT / ".env.vm")
        if not key:
            raise SystemExit("--nemotron needs NVIDIA_API_KEY")
        nemo = NemotronEncoder(key)
        encoders["nemotron"] = (
            nemo,
            nemo.passages(passage_texts(graph, tagged=True), cache / "nemotron_tagged.npy"),
        )
        encoders["nemotron-plain"] = (
            nemo,
            nemo.passages(passage_texts(graph, tagged=False), cache / "nemotron_plain.npy"),
        )

    replica = Replica(graph, FullText(graph), encoders, settings)
    detector = EmotionDetector(settings)
    classifier = IntentClassifier(llm=None, settings=settings, use_llm=False)

    prepared = []
    for pair in pairs:
        query = str(pair["query"])
        emotion = detector.detect(query)
        intent = await classifier.classify(query, emotion)
        prepared.append((pair, query, emotion, intent))

    results: dict[str, Any] = {}
    per_query: dict[str, list[Any]] = {}
    for cfg in configurations(args.nemotron):
        rows = []
        for pair, query, emotion, intent in prepared:
            ids = replica.retrieve(cfg, query, emotion, intent, args.k)
            rows.append(evaluate_one(pair, ids, intent.intent.value, args.k))
        per_query[cfg.name] = rows
        hits = sum(1 for row in rows if row.hits)
        low, high = wilson(hits, len(rows))
        by_cat: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            by_cat[row.category].append(1 if row.hits else 0)
        results[cfg.name] = {
            "config": cfg.__dict__,
            "hits": hits,
            "n": len(rows),
            "hit_rate": hits / len(rows),
            "hit_ci95": [round(low, 3), round(high, 3)],
            "recall_at_k": _mean([row.recall_at_k for row in rows]),
            "mrr": _mean([row.reciprocal_rank for row in rows]),
            "ndcg_at_k": _mean([row.ndcg_at_k for row in rows]),
            "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())},
            "hit_ids": [row.id for row in rows if row.hits],
        }

    base = per_query["production replica"]
    for name, rows in per_query.items():
        if name == "production replica":
            continue
        a_only = sum(1 for b, r in zip(base, rows, strict=True) if b.hits and not r.hits)
        b_only = sum(1 for b, r in zip(base, rows, strict=True) if r.hits and not b.hits)
        results[name]["vs_production"] = {
            "hit_gained": b_only,
            "hit_lost": a_only,
            "mcnemar_p": round(mcnemar_exact(a_only, b_only), 4),
            "mrr_diff_ci95": [
                round(x, 4)
                for x in paired_bootstrap(
                    [x.reciprocal_rank for x in base], [x.reciprocal_rank for x in rows]
                )
            ],
            "ndcg_diff_ci95": [
                round(x, 4)
                for x in paired_bootstrap([x.ndcg_at_k for x in base], [x.ndcg_at_k for x in rows])
            ],
        }

    return {
        "provenance": {
            "harness": "eval/baselines.py (offline replica of neo4j-hybrid-cypher)",
            "graph": str(EXPORT_DIR.relative_to(PROJECT_ROOT)),
            "verses": len(graph.ids),
            "encoder": minilm.name,
            "stored_vector_parity_cosine": round(parity, 6),
            "intent": "deterministic (keyword prior)",
            "k": args.k,
            "gold": Path(args.gold).name,
            "queries_scored": len(pairs),
            "queries_without_relevant_verse": unanswerable,
            "git_commit": git_commit(),
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "results": results,
    }


def _key_from(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("NVIDIA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def render(report: dict[str, Any]) -> str:
    p = report["provenance"]
    lines = [
        f"Baselines over {p['graph']} ({p['verses']} verses), k={p['k']}, {p['intent']}",
        f"encoder {p['encoder']}; stored-vector parity cosine {p['stored_vector_parity_cosine']}",
        f"commit {p['git_commit']}  measured {p['measured']}",
        "",
        f"{'configuration':44} {'hit':>11} {'95% CI':>13} {'R@5':>6} {'MRR':>6} "
        f"{'nDCG':>6} {'+/-':>6} {'p':>6}",
    ]
    for name, row in report["results"].items():
        versus = row.get("vs_production")
        delta = f"+{versus['hit_gained']}/-{versus['hit_lost']}" if versus else "-"
        pvalue = f"{versus['mcnemar_p']:.3f}" if versus else "-"
        lines.append(
            f"{name:44} {row['hits']:>2}/{row['n']} {row['hit_rate']:.3f} "
            f"{row['hit_ci95'][0]:.2f}-{row['hit_ci95'][1]:.2f} "
            f"{row['recall_at_k']:6.3f} {row['mrr']:6.3f} {row['ndcg_at_k']:6.3f} "
            f"{delta:>6} {pvalue:>6}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--gold", default=str(GOLD_PATH))
    parser.add_argument("--nemotron", action="store_true", help="add the NIM encoder rows")
    parser.add_argument(
        "--cache",
        default=str(PROJECT_ROOT / "logs" / "baseline_vectors"),
        help="directory for re-embedded passage vectors (git-ignored)",
    )
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args(argv)

    report = asyncio.run(run(args))
    print(render(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
