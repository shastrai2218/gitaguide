"""Pooled relevance annotation for a 100-query, two-annotator gold set.

Research-pipeline role
---------------------
The 40-query gold set has two weaknesses a reviewer will name: one person chose
the relevant verses, and a verse nobody listed counts as irrelevant even when it
is a good answer. This kit fixes both the way TREC does it.

``build``  pools the top 5 verses of three different retrievers for each of the
           100 queries (the 40 existing ones plus ``gold_queries_v2.jsonl``),
           shuffles them so no annotator can tell which system found a verse,
           and writes one CSV per annotator.
``score``  reads the two completed CSVs, reports agreement (Cohen's kappa,
           unweighted on relevant / not relevant and quadratic-weighted on the
           0/1/2 grades), writes the adjudication list, and builds
           ``gold_pairs_v2.json`` from the agreed judgements.

Grades: 2 = directly addresses the question, 1 = relevant but indirect,
0 = not relevant. Instructions for annotators are in ``ANNOTATION_GUIDE.md``.

Usage
-----
::

    GITAGUIDE_MOCK_MODE=false python -m eval.annotation_kit build --out annotation/
    python -m eval.annotation_kit score annotation/annotator_A.csv annotation/annotator_B.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
NEW_QUERIES = HERE / "gold_queries_v2.jsonl"
POOL_DEPTH = 5
FIELDS = ["item", "query_id", "query", "verse_id", "verse_text", "grade", "note"]


def all_queries() -> list[dict[str, Any]]:
    from eval.retrieval_eval import load_gold

    queries = [
        {
            "id": p["id"],
            "category": p.get("category", ""),
            "expected_intent": p.get("expected_intent", ""),
            "query": p["query"],
        }
        for p in load_gold()["pairs"]
    ]
    with NEW_QUERIES.open(encoding="utf-8") as handle:
        queries += [json.loads(line) for line in handle if line.strip()]
    return queries


async def pool(queries: list[dict[str, Any]], nemotron: bool) -> dict[str, list[str]]:
    """Top ``POOL_DEPTH`` of each system per query, deduplicated."""
    import os
    from pathlib import Path as _P

    from app.config import PROJECT_ROOT, get_settings
    from app.pipeline.emotion import EmotionDetector
    from app.pipeline.intent import IntentClassifier
    from eval import baselines as b

    settings = get_settings().model_copy()
    graph = b.load_export()
    minilm = b.MiniLMQueryEncoder(settings)
    encoders: dict[str, Any] = {"minilm": (minilm, graph.vectors)}
    systems = [
        b.Config("production replica"),
        b.Config("vector raw", fulltext=False, expand=False, rerank=False),
    ]
    if nemotron:
        key = os.environ.get("NVIDIA_API_KEY") or b._key_from(PROJECT_ROOT / ".env.vm")
        nemo = b.NemotronEncoder(key)
        cache = _P(PROJECT_ROOT / "logs" / "baseline_vectors" / "nemotron_plain.npy")
        encoders["nemotron-plain"] = (nemo, nemo.passages(b.passage_texts(graph, False), cache))
        systems.append(
            b.Config(
                "1B untagged raw",
                fulltext=False,
                expand=False,
                rerank=False,
                encoder="nemotron-plain",
            )
        )
    replica = b.Replica(graph, b.FullText(graph), encoders, settings)
    detector = EmotionDetector(settings)
    classifier = IntentClassifier(llm=None, settings=settings, use_llm=False)

    pooled: dict[str, list[str]] = {}
    for q in queries:
        emotion = detector.detect(q["query"])
        intent = await classifier.classify(q["query"], emotion)
        seen: list[str] = []
        for cfg in systems:
            for verse_id in replica.retrieve(cfg, q["query"], emotion, intent, POOL_DEPTH):
                if verse_id not in seen:
                    seen.append(verse_id)
        pooled[q["id"]] = seen
    return pooled


def build(args: argparse.Namespace) -> int:
    import asyncio

    from eval.baselines import load_export

    queries = all_queries()
    pooled = asyncio.run(pool(queries, nemotron=not args.no_nemotron))
    text = load_export().text
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    items = [
        (q["id"], q["query"], verse_id, text[verse_id])
        for q in queries
        for verse_id in pooled[q["id"]]
    ]
    for name, seed in (("A", 11), ("B", 29)):
        order = items[:]
        # Queries stay together (reading a question once is faster), but the
        # verses under each question are shuffled per annotator, so position
        # carries no hint of which system - or which rank - produced a verse.
        by_query: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for row in order:
            by_query[row[0]].append(row)
        rng = random.Random(seed)
        rows = []
        for q in queries:
            group = by_query[q["id"]]
            rng.shuffle(group)
            rows.extend(group)
        with (out / f"annotator_{name}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(FIELDS)
            for index, (qid, query, verse_id, verse_text) in enumerate(rows, start=1):
                writer.writerow([index, qid, query, verse_id, verse_text, "", ""])
    (out / "queries.json").write_text(
        json.dumps(queries, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "pool.json").write_text(json.dumps(pooled, indent=2), encoding="utf-8")
    sizes = [len(v) for v in pooled.values()]
    print(
        f"{len(queries)} queries, {len(items)} judgements per annotator "
        f"({min(sizes)}-{max(sizes)} verses per query) -> {out}"
    )
    return 0


def read_grades(path: Path) -> dict[tuple[str, str], int]:
    grades: dict[tuple[str, str], int] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            value = (row.get("grade") or "").strip()
            if value not in {"0", "1", "2"}:
                raise SystemExit(
                    f"{path.name} item {row.get('item')}: grade must be 0, 1 or 2, got {value!r}"
                )
            grades[(row["query_id"], row["verse_id"])] = int(value)
    return grades


def kappa(a: list[int], b: list[int], weights: str | None = None) -> float:
    """Cohen's kappa; ``weights='quadratic'`` for ordinal grades."""
    cats = sorted(set(a) | set(b))
    k = len(cats)
    index = {c: i for i, c in enumerate(cats)}
    n = len(a)
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        observed[index[x]][index[y]] += 1 / n
    row = [sum(r) for r in observed]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]

    def w(i: int, j: int) -> float:
        if weights == "quadratic":
            return ((i - j) / max(1, k - 1)) ** 2
        return 0.0 if i == j else 1.0

    disagreement_obs = sum(w(i, j) * observed[i][j] for i in range(k) for j in range(k))
    disagreement_exp = sum(w(i, j) * row[i] * col[j] for i in range(k) for j in range(k))
    return 1.0 if disagreement_exp == 0 else 1 - disagreement_obs / disagreement_exp


def read_notes(path: Path) -> dict[tuple[str, str], str]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {
            (row["query_id"], row["verse_id"]): (row.get("note") or "").strip().lower()
            for row in csv.DictReader(fh)
        }


def score(args: argparse.Namespace) -> int:
    a, b = read_grades(Path(args.a)), read_grades(Path(args.b))
    keys = sorted(set(a) & set(b))
    if set(a) != set(b):
        raise SystemExit("the two files do not grade the same items")
    ga, gb = [a[k] for k in keys], [b[k] for k in keys]
    binary = kappa([int(x > 0) for x in ga], [int(x > 0) for x in gb])
    weighted = kappa(ga, gb, "quadratic")
    exact = sum(x == y for x, y in zip(ga, gb, strict=True)) / len(keys)
    print(f"{len(keys)} judgements; exact agreement {exact:.3f}")
    # Identical free-text notes on the same item are the signature of shared
    # work. Kappa only measures agreement between *independent* judges, so
    # when they appear the files are reported as one consensus annotation.
    na, nb = read_notes(Path(args.a)), read_notes(Path(args.b))
    shared_notes = sum(1 for k in keys if na.get(k) and na.get(k) == nb.get(k))
    print(f"identical non-empty notes on the same item: {shared_notes}")
    if args.consensus:
        print("consensus mode: the files are one joint annotation; kappa is not reported")
    else:
        print(f"Cohen's kappa, relevant vs not: {binary:.3f}")
        print(f"Cohen's kappa, quadratic-weighted 0/1/2: {weighted:.3f}")
        if shared_notes:
            print(
                "WARNING: shared notes mean these judges were not independent; "
                "re-run with --consensus and measure agreement with `sample`/`check`"
            )

    out = Path(args.a).parent
    adjudicated = out / "adjudicate.csv"
    # Keep any final grades already entered: re-running must never erase them.
    decided: dict[tuple[str, str], int] = {}
    if adjudicated.exists():
        with adjudicated.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("final_grade") or "").strip() in {"0", "1", "2"}:
                    decided[(row["query_id"], row["verse_id"])] = int(row["final_grade"])
    disputed = [k for k in keys if (a[k] > 0) != (b[k] > 0)]
    with adjudicated.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["query_id", "verse_id", "grade_A", "grade_B", "final_grade"])
        for k in disputed:
            writer.writerow([k[0], k[1], a[k], b[k], decided.get(k, "")])
    decided = {k: v for k, v in decided.items() if k in set(disputed)}
    print(f"{len(disputed)} relevance disagreements -> {adjudicated}")

    final: dict[tuple[str, str], int] = {}
    for k in keys:
        if k in decided:
            final[k] = decided[k]
        elif (a[k] > 0) == (b[k] > 0):
            final[k] = min(a[k], b[k])  # agreed; the lower grade when they differ
    pending = len(disputed) - len(decided)

    queries = json.loads((out / "queries.json").read_text(encoding="utf-8"))
    pairs = []
    for q in queries:
        graded = {vid: g for (qid, vid), g in final.items() if qid == q["id"] and g > 0}
        pairs.append({**q, "relevant": sorted(graded), "graded": graded})
    how = (
        "two annotators working jointly (consensus labels; independence not claimed)"
        if args.consensus
        else f"two independent annotators, kappa relevant/not {binary:.3f}, "
        f"quadratic {weighted:.3f}"
    )
    gold = {
        "description": (
            f"Pooled gold set (pool depth 5 from three retrievers), labelled by {how}; "
            f"exact agreement {exact:.3f}; {pending} disagreements not yet adjudicated."
        ),
        "corpus": ["data/graph_export/verses.jsonl"],
        "pairs": pairs,
    }
    target = HERE / "gold_pairs_v2.json"
    target.write_text(json.dumps(gold, indent=2, ensure_ascii=False), encoding="utf-8")
    empty = sum(1 for p in pairs if not p["relevant"])
    print(f"wrote {target} ({len(pairs)} queries, {empty} with no relevant verse)")
    if pending:
        print(f"NOTE: fill final_grade for {pending} rows in adjudicate.csv and re-run")
    return 0


def sample(args: argparse.Namespace) -> int:
    """A blank, freshly shuffled subset for one annotator working alone."""
    with Path(args.template).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    rng = random.Random(args.seed)
    picked = rng.sample(rows, min(args.n, len(rows)))
    with Path(args.out).open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for index, row in enumerate(picked, start=1):
            writer.writerow(
                [index, row["query_id"], row["query"], row["verse_id"], row["verse_text"], "", ""]
            )
    print(f"{len(picked)} blank rows -> {args.out}")
    return 0


def check(args: argparse.Namespace) -> int:
    """Agreement of an independent annotator with the consensus labels."""
    solo = read_grades(Path(args.solo))
    a, b = read_grades(Path(args.a)), read_grades(Path(args.b))
    keys = sorted(k for k in solo if k in a and k in b and (a[k] > 0) == (b[k] > 0))
    consensus = [min(a[k], b[k]) for k in keys]
    mine = [solo[k] for k in keys]
    print(f"{len(keys)} items (consensus disagreements left out)")
    print(
        f"exact agreement {sum(x == y for x, y in zip(mine, consensus, strict=True)) / len(keys):.3f}"
    )
    print(
        f"Cohen's kappa, relevant vs not: "
        f"{kappa([int(x > 0) for x in mine], [int(x > 0) for x in consensus]):.3f}"
    )
    print(f"Cohen's kappa, quadratic-weighted 0/1/2: {kappa(mine, consensus, 'quadratic'):.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build")
    p_build.add_argument("--out", default="annotation")
    p_build.add_argument("--no-nemotron", action="store_true")
    p_score = sub.add_parser("score")
    p_score.add_argument("a")
    p_score.add_argument("b")
    p_score.add_argument(
        "--consensus", action="store_true", help="the files are one joint annotation"
    )
    p_sample = sub.add_parser("sample")
    p_sample.add_argument("template")
    p_sample.add_argument("--n", type=int, default=150)
    p_sample.add_argument("--seed", type=int, default=41)
    p_sample.add_argument("--out", default="annotation/independent_check.csv")
    p_check = sub.add_parser("check")
    p_check.add_argument("solo")
    p_check.add_argument("a")
    p_check.add_argument("b")
    args = parser.parse_args(argv)
    commands = {"build": build, "score": score, "sample": sample, "check": check}
    return commands[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
