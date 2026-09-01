"""Retrieval evaluation: Recall@k, MRR, nDCG@k and intent accuracy.

Research-pipeline role
---------------------
This is the measurement half of the retrieval contribution. It runs the *real*
pipeline components (emotion -> intent -> retrieval) over ``gold_pairs.json`` and
reports per-query and aggregate numbers, so an ablation is a one-line change:

    python -m eval.retrieval_eval                          # full hybrid
    python -m eval.retrieval_eval --no-graph               # vector+lexical only
    python -m eval.retrieval_eval --no-intent-boost        # no scripture prior
    python -m eval.retrieval_eval --k 3 --json results.json

Because the offline embedder is a hashing embedder, absolute numbers in mock
mode are a floor, not a result; run with ``GITAGUIDE_MOCK_MODE=false`` and real
MiniLM embeddings for numbers worth reporting. The harness prints which backend
produced the numbers to keep the two from being confused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, Settings, get_settings
from app.llm.factory import build_llm_provider
from app.pipeline.emotion import EmotionDetector
from app.pipeline.intent import IntentClassifier
from app.pipeline.retrieval import HybridRetriever, InMemoryHybridRetriever, build_retriever
from corpus.schemas import Corpus

logger = logging.getLogger(__name__)

GOLD_PATH = Path(__file__).with_name("gold_pairs.json")


@dataclass
class QueryResult:
    """Per-query evaluation record."""

    id: str
    query: str
    expected_intent: str
    predicted_intent: str
    relevant: list[str]
    retrieved: list[str]
    #: Stratum from the gold set (emotional / professional / statecraft / ...).
    category: str = ""
    hits: list[str] = field(default_factory=list)
    reciprocal_rank: float = 0.0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    ndcg_at_k: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "expected_intent": self.expected_intent,
            "predicted_intent": self.predicted_intent,
            "category": self.category,
            "relevant": self.relevant,
            "retrieved": self.retrieved,
            "hits": self.hits,
            "recall_at_k": round(self.recall_at_k, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "reciprocal_rank": round(self.reciprocal_rank, 4),
            "ndcg_at_k": round(self.ndcg_at_k, 4),
        }


@dataclass
class EvalReport:
    """Aggregate metrics plus the per-query detail."""

    k: int
    backend: str
    embedder: str
    queries: list[QueryResult] = field(default_factory=list)
    ablations: dict[str, Any] = field(default_factory=dict)
    #: backend, embedder, corpus size, timestamp, git commit. A metrics table
    #: without these five is unquotable: the same harness produces
    #: non-comparable numbers depending on which retriever ran.
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def recall_at_k(self) -> float:
        return _mean([item.recall_at_k for item in self.queries])

    @property
    def precision_at_k(self) -> float:
        return _mean([item.precision_at_k for item in self.queries])

    @property
    def mrr(self) -> float:
        return _mean([item.reciprocal_rank for item in self.queries])

    @property
    def ndcg_at_k(self) -> float:
        return _mean([item.ndcg_at_k for item in self.queries])

    @property
    def intent_accuracy(self) -> float:
        if not self.queries:
            return 0.0
        correct = sum(
            1 for item in self.queries if item.predicted_intent == item.expected_intent
        )
        return correct / len(self.queries)

    def by_category(self) -> dict[str, dict[str, float]]:
        """Metrics grouped by the gold set's ``category`` stratum."""
        groups: dict[str, list[QueryResult]] = {}
        for item in self.queries:
            groups.setdefault(item.category or "uncategorised", []).append(item)
        out: dict[str, dict[str, float]] = {}
        for name, items in sorted(groups.items()):
            out[name] = {
                "n": len(items),
                "hit_rate": _mean([1.0 if i.hits else 0.0 for i in items]),
                "recall_at_k": _mean([i.recall_at_k for i in items]),
                "mrr": _mean([i.reciprocal_rank for i in items]),
                "ndcg_at_k": _mean([i.ndcg_at_k for i in items]),
                "intent_accuracy": _mean(
                    [1.0 if i.predicted_intent == i.expected_intent else 0.0 for i in items]
                ),
            }
        return out

    @property
    def hit_rate(self) -> float:
        """Fraction of queries with at least one relevant verse in the top k."""
        if not self.queries:
            return 0.0
        return sum(1 for item in self.queries if item.hits) / len(self.queries)

    def as_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "backend": self.backend,
            "embedder": self.embedder,
            "provenance": self.provenance,
            "ablations": self.ablations,
            "n_queries": len(self.queries),
            "recall_at_k": round(self.recall_at_k, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "mrr": round(self.mrr, 4),
            "ndcg_at_k": round(self.ndcg_at_k, 4),
            "hit_rate": round(self.hit_rate, 4),
            "intent_accuracy": round(self.intent_accuracy, 4),
            "queries": [item.as_dict() for item in self.queries],
        }

    def render(self) -> str:
        lines = [
            "",
            f"Retrieval evaluation (k={self.k})",
            f"  backend      : {self.backend}",
            f"  embedder     : {self.embedder}",
            f"  corpus       : {self.provenance.get('corpus_size', '?')} verses",
            f"  commit       : {self.provenance.get('git_commit', '?')}",
            f"  measured     : {self.provenance.get('timestamp', '?')}",
            f"  ablations    : {self.ablations or 'none'}",
            f"  queries      : {len(self.queries)}",
            "",
            f"  Recall@{self.k}     : {self.recall_at_k:.3f}",
            f"  Precision@{self.k}  : {self.precision_at_k:.3f}",
            f"  MRR          : {self.mrr:.3f}",
            f"  nDCG@{self.k}       : {self.ndcg_at_k:.3f}",
            f"  Hit rate     : {self.hit_rate:.3f}",
            f"  Intent acc.  : {self.intent_accuracy:.3f}",
            "",
            "  per query:",
        ]
        for item in self.queries:
            marker = "HIT " if item.hits else "MISS"
            lines.append(
                f"   [{marker}] {item.id} rr={item.reciprocal_rank:.2f} "
                f"recall={item.recall_at_k:.2f} intent={item.predicted_intent}"
                f"{'' if item.predicted_intent == item.expected_intent else f' (want {item.expected_intent})'}"
            )
            lines.append(f"           retrieved: {', '.join(item.retrieved) or '-'}")
        return "\n".join(lines)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def load_gold(path: Path = GOLD_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_commit() -> str:
    """Short SHA of the working tree, or an explicit marker if unavailable."""
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    return f"{sha}-dirty" if dirty else sha


#: Delimiters for the block this harness owns inside the review document.
RESULTS_BEGIN = "<!-- eval-results:begin -->"
RESULTS_END = "<!-- eval-results:end -->"


def render_results_markdown(report: EvalReport) -> str:
    """The provenance + metrics block written into the review document."""
    p = report.provenance
    lines = [
        RESULTS_BEGIN,
        "## Latest evaluation run",
        "",
        "| field | value |",
        "| --- | --- |",
        f"| backend | `{p.get('backend', '?')}` |",
        f"| embedder | `{p.get('embedder', '?')}` |",
        f"| corpus size | {p.get('corpus_size', '?')} verses |",
        f"| measured | {p.get('timestamp', '?')} |",
        f"| git commit | `{p.get('git_commit', '?')}` |",
        f"| k | {report.k} |",
        f"| ablations | {report.ablations or 'none'} |",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| hit rate | {report.hit_rate:.3f} |",
        f"| Recall@{report.k} | {report.recall_at_k:.3f} |",
        f"| MRR | {report.mrr:.3f} |",
        f"| nDCG@{report.k} | {report.ndcg_at_k:.3f} |",
        f"| Precision@{report.k} | {report.precision_at_k:.3f} |",
        f"| intent accuracy | {report.intent_accuracy:.3f} |",
        "",
        "| category | n | hit | recall | MRR | nDCG | intent |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in report.by_category().items():
        lines.append(
            f"| {name} | {int(m['n'])} | {m['hit_rate']:.2f} | {m['recall_at_k']:.3f} | "
            f"{m['mrr']:.3f} | {m['ndcg_at_k']:.3f} | {m['intent_accuracy']:.2f} |"
        )
    lines += [
        "",
        "A metrics table without backend, embedder, corpus size, timestamp and commit "
        "is unquotable - the same harness produces non-comparable numbers depending on "
        "which retriever ran. See `docs/kg_measurements.md` section 3.",
        RESULTS_END,
    ]
    return "\n".join(lines)


def record_results_markdown(report: EvalReport, path: Path) -> None:
    """Insert or replace the harness-owned results block in a markdown file."""
    block = render_results_markdown(report)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if RESULTS_BEGIN in text and RESULTS_END in text:
            head, rest = text.split(RESULTS_BEGIN, 1)
            _, tail = rest.split(RESULTS_END, 1)
            text = f"{head}{block}{tail}"
        else:
            text = f"{text.rstrip()}\n\n{block}\n"
    else:
        text = f"{block}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    logger.info("recorded evaluation provenance in %s", path)


def _load_gold_corpus(gold: dict[str, Any]) -> Corpus | None:
    """Load the corpus file(s) the gold set was written against.

    Accepts a single path or a list, so a gold set can span both scriptures.
    """
    declared = gold.get("corpus")
    if not declared:
        return None
    paths = [declared] if isinstance(declared, str) else list(declared)
    corpus = Corpus()
    for raw in paths:
        path = Path(raw)
        if path.exists():
            corpus.extend(Corpus.load(path))
        else:
            logger.warning("gold set declares corpus %s, which does not exist", path)
    return corpus if len(corpus) else None


def fingerprint_of(text: str, words: int = 8) -> str:
    """First ``words`` words of a verse, lowercased - the staleness key.

    Verse ids are addresses, not identities: Arthashastra sutra numbers are
    *positional*, so re-segmenting the corpus silently moves them (three of the
    five AS ids in the original gold set ended up pointing at different sutras,
    and one vanished). A fingerprint of the text the id pointed at when the gold
    entry was written turns that silent rot into a loud warning.
    """
    return " ".join(str(text).lower().split()[:words])


def check_gold_staleness(
    gold: dict[str, Any],
    corpus: Corpus,
    words: int = 8,
) -> list[str]:
    """Compare every gold id's stored fingerprint against the live corpus."""
    by_id = {verse.id: verse for verse in corpus}
    problems: list[str] = []
    for pair in gold.get("pairs", []):
        prints = pair.get("fingerprints") or {}
        for verse_id in pair.get("relevant", []):
            verse = by_id.get(verse_id)
            if verse is None:
                problems.append(f"{pair.get('id')}: {verse_id} NO LONGER EXISTS in the corpus")
                continue
            expected = prints.get(verse_id)
            if not expected:
                problems.append(f"{pair.get('id')}: {verse_id} has no stored fingerprint")
                continue
            actual = fingerprint_of(verse.text_english, words)
            if actual != fingerprint_of(expected, words):
                problems.append(
                    f"{pair.get('id')}: {verse_id} MOVED - gold fingerprint "
                    f"{expected!r} but corpus now says {actual!r}"
                )
    return problems


def dcg(gains: Sequence[float]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def evaluate_one(
    pair: dict[str, Any],
    retrieved: Sequence[str],
    predicted_intent: str,
    k: int,
) -> QueryResult:
    """Compute the metrics for a single query."""
    relevant = list(pair.get("relevant", []))
    graded: dict[str, float] = {
        key: float(value) for key, value in (pair.get("graded") or {}).items()
    }
    for verse_id in relevant:
        graded.setdefault(verse_id, 2.0)

    top = list(retrieved[:k])
    hits = [verse_id for verse_id in top if verse_id in set(relevant)]

    reciprocal_rank = 0.0
    for index, verse_id in enumerate(top, start=1):
        if verse_id in set(relevant):
            reciprocal_rank = 1.0 / index
            break

    ideal = sorted(graded.values(), reverse=True)[:k]
    actual = [graded.get(verse_id, 0.0) for verse_id in top]
    ideal_dcg = dcg(ideal)

    return QueryResult(
        id=str(pair.get("id", "")),
        query=str(pair.get("query", "")),
        expected_intent=str(pair.get("expected_intent", "")),
        predicted_intent=predicted_intent,
        category=str(pair.get("category", "")),
        relevant=relevant,
        retrieved=top,
        hits=hits,
        reciprocal_rank=reciprocal_rank,
        recall_at_k=len(hits) / len(relevant) if relevant else 0.0,
        precision_at_k=len(hits) / len(top) if top else 0.0,
        ndcg_at_k=(dcg(actual) / ideal_dcg) if ideal_dcg else 0.0,
    )


async def run_evaluation(
    k: int = 5,
    gold_path: Path = GOLD_PATH,
    settings: Settings | None = None,
    retriever: HybridRetriever | None = None,
    disable_graph: bool = False,
    disable_intent_boost: bool = False,
) -> EvalReport:
    """Run the pipeline over every gold pair and aggregate the metrics."""
    settings = (settings or get_settings()).model_copy()
    ablations: dict[str, Any] = {}
    if disable_graph:
        settings.graph_expansion_hops = 0
        settings.graph_path_boost = 0.0
        ablations["graph"] = "disabled"
    if disable_intent_boost:
        settings.scripture_intent_boost = 0.0
        ablations["intent_boost"] = "disabled"

    gold = load_gold(gold_path)
    corpus = _load_gold_corpus(gold)

    if corpus is not None and len(corpus):
        problems = check_gold_staleness(gold, corpus)
        if problems:
            logger.error(
                "GOLD SET STALENESS: %d gold id(s) no longer match the corpus they "
                "were written against. Metrics computed against them are not "
                "comparable with earlier runs:\n  %s",
                len(problems),
                "\n  ".join(problems),
            )
        else:
            logger.info(
                "gold set fingerprints verified against %d corpus verses", len(corpus)
            )

    if retriever is None:
        retriever = (
            # strict: a measurement must never silently swap which system it
            # measured. The application path keeps the graceful fallback.
            build_retriever(settings, corpus=corpus, strict=True)
            if settings.neo4j_enabled
            else InMemoryHybridRetriever(corpus=corpus, settings=settings)
        )

    detector = EmotionDetector(settings)
    classifier = IntentClassifier(llm=build_llm_provider(settings), settings=settings)

    results: list[QueryResult] = []
    for pair in gold.get("pairs", []):
        query = str(pair.get("query", ""))
        emotion = detector.detect(query)
        intent = await classifier.classify(query, emotion)
        trace = await retriever.retrieve(query, emotion, intent, top_k=k)
        results.append(evaluate_one(pair, trace.verse_ids, intent.intent.value, k))

    embedder_name = getattr(getattr(retriever, "embedder", None), "name", "n/a")
    return EvalReport(
        k=k,
        backend=retriever.backend,
        embedder=embedder_name,
        queries=results,
        ablations=ablations,
        provenance={
            "backend": retriever.backend,
            "embedder": embedder_name,
            "corpus_size": len(corpus) if corpus is not None else 0,
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_commit": git_commit(),
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate GitaGuide retrieval")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--gold", default=str(GOLD_PATH))
    parser.add_argument("--no-graph", action="store_true", help="ablate graph expansion")
    parser.add_argument(
        "--no-intent-boost", action="store_true", help="ablate the scripture prior"
    )
    parser.add_argument("--json", dest="json_out", default=None, help="write metrics as JSON")
    parser.add_argument(
        "--record-md",
        default=str(PROJECT_ROOT / "docs" / "gold_set_review.md"),
        help="markdown file to record the provenance + metrics block in",
    )
    parser.add_argument(
        "--no-record", action="store_true", help="do not touch the markdown record"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")

    report = asyncio.run(
        run_evaluation(
            k=args.k,
            gold_path=Path(args.gold),
            disable_graph=args.no_graph,
            disable_intent_boost=args.no_intent_boost,
        )
    )
    print(report.render())

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.as_dict(), indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json_out}")

    if not args.no_record:
        record_results_markdown(report, Path(args.record_md))
        print(f"recorded provenance + metrics in {args.record_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
