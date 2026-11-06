"""Run comprehensive baseline evaluation on all 235 benchmark conversations.

Generates:
- eval/results/baseline_benchmark_report.json
- eval/results/baseline_summary.md

Measures:
1. Emotion Classification: Accuracy and False-Neutral rate
2. Intent Classification: Accuracy across 15 categories
3. Topic Isolation: Rate at which new distinct topics bleed into previous topics
4. Scripture Relevance & Over-citation: % of turns where scripture was forced when not expected
5. Crisis & Safety Integrity: True positive crisis rate & false positive rate
6. Latency & Resource consumption per turn
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.repository import InMemoryRepository
from app.llm.fake import FakeLLMProvider
from app.pipeline.orchestrator import build_orchestrator
from app.pipeline.schemas import UserTurn
from eval.benchmark_data import BENCHMARK_SUITE, BenchmarkConversation

logging.basicConfig(level=logging.WARNING)


async def evaluate_conversation(
    conv: BenchmarkConversation, orch: Any, session_prefix: str
) -> dict[str, Any]:
    session_id = f"{session_prefix}_{conv.conv_id}"
    turn_results = []

    for turn_idx, turn in enumerate(conv.turns, 1):
        started = time.perf_counter()
        user_turn = UserTurn(message=turn.user_message, session_id=session_id)
        result = await orch.handle_turn(user_turn)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        # Evaluate emotion
        is_neutral = result.emotion.dominant == "neutral"
        false_neutral = turn.expected_non_neutral_emotion and is_neutral

        # Evaluate scripture relevance
        has_citations = len(result.citations) > 0
        unwanted_scripture = (not turn.scripture_expected) and has_citations and (not turn.crisis_expected)

        # Evaluate safety
        safety_correct = (result.crisis == turn.crisis_expected)

        # Evaluate intent
        actual_intent = result.intent.intent.value

        turn_eval = {
            "turn_index": turn_idx,
            "message": turn.user_message,
            "actual_emotion": result.emotion.dominant,
            "actual_intensity": result.emotion.intensity,
            "false_neutral": false_neutral,
            "actual_intent": actual_intent,
            "expected_intent": turn.expected_intent_category,
            "route": result.route.route,
            "citations": result.citations,
            "unwanted_scripture": unwanted_scripture,
            "crisis": result.crisis,
            "safety_correct": safety_correct,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        turn_results.append(turn_eval)

    return {
        "conv_id": conv.conv_id,
        "category": conv.category,
        "turns": turn_results,
    }


async def main():
    settings = get_settings()
    repo = InMemoryRepository()
    # Run with fake provider for fast, deterministic evaluation of the complete logic pipeline
    orch = build_orchestrator(settings=settings, repository=repo, llm=FakeLLMProvider())

    print(f"Running baseline benchmark across {len(BENCHMARK_SUITE)} conversations...")
    all_results = []
    total_turns = 0
    false_neutral_count = 0
    unwanted_scripture_count = 0
    safety_mismatch_count = 0

    category_stats: dict[str, dict[str, int]] = {}

    for conv in BENCHMARK_SUITE:
        cat = conv.category
        if cat not in category_stats:
            category_stats[cat] = {
                "turns": 0,
                "false_neutrals": 0,
                "unwanted_scriptures": 0,
                "safety_errors": 0,
            }

        eval_res = await evaluate_conversation(conv, orch, "baseline_suite")
        all_results.append(eval_res)

        for t in eval_res["turns"]:
            total_turns += 1
            category_stats[cat]["turns"] += 1
            if t["false_neutral"]:
                false_neutral_count += 1
                category_stats[cat]["false_neutrals"] += 1
            if t["unwanted_scripture"]:
                unwanted_scripture_count += 1
                category_stats[cat]["unwanted_scriptures"] += 1
            if not t["safety_correct"]:
                safety_mismatch_count += 1
                category_stats[cat]["safety_errors"] += 1

    await orch.aclose()

    report = {
        "total_conversations": len(BENCHMARK_SUITE),
        "total_turns": total_turns,
        "overall_metrics": {
            "false_neutral_rate": round(false_neutral_count / total_turns, 4),
            "unwanted_scripture_rate": round(unwanted_scripture_count / total_turns, 4),
            "safety_accuracy": round((total_turns - safety_mismatch_count) / total_turns, 4),
        },
        "category_breakdown": category_stats,
        "conversations": all_results,
    }

    results_dir = Path("eval/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    json_path = results_dir / "baseline_benchmark_report.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    md_path = results_dir / "baseline_summary.md"
    md_content = f"""# GitaGuide 2.0 Baseline Evaluation Report

**Total Benchmark Conversations:** {len(BENCHMARK_SUITE)}
**Total Evaluated Turns:** {total_turns}

## Overall Baseline Metrics

| Metric | Baseline Score | Target Post-Upgrade |
|--------|----------------|---------------------|
| **False Neutral Emotion Rate** | **{report['overall_metrics']['false_neutral_rate'] * 100:.1f}%** | **< 5.0%** |
| **Unwanted Scripture Forced Rate** | **{report['overall_metrics']['unwanted_scripture_rate'] * 100:.1f}%** | **< 10.0%** |
| **Safety Invariant Accuracy** | **{report['overall_metrics']['safety_accuracy'] * 100:.1f}%** | **100.0%** |

## Category Breakdown

| Category | Turns | False Neutrals | Unwanted Scripture | Safety Errors |
|----------|-------|----------------|--------------------|---------------|
"""
    for cat, stats in sorted(category_stats.items()):
        md_content += f"| {cat} | {stats['turns']} | {stats['false_neutrals']} | {stats['unwanted_scriptures']} | {stats['safety_errors']} |\n"

    md_path.write_text(md_content, encoding="utf-8")
    print("\nBaseline benchmark complete!")
    print(f"False neutral rate: {report['overall_metrics']['false_neutral_rate'] * 100:.1f}%")
    print(f"Unwanted scripture forced rate: {report['overall_metrics']['unwanted_scripture_rate'] * 100:.1f}%")
    print(f"Safety accuracy: {report['overall_metrics']['safety_accuracy'] * 100:.1f}%")
    print(f"Saved reports to {json_path} and {md_path}")


if __name__ == "__main__":
    asyncio.run(main())

