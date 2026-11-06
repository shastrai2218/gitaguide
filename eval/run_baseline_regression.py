"""Phase 2 Baseline Evaluation Script - Runs the 6-turn regression conversation

Captures:
- Raw emotion and intensity
- Intent label and confidence
- Route and stage
- Citations returned
- Citations in answer
- Generated reply
- Memory state & rolling summary
- Topic separation (or lack thereof)
"""

import asyncio
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.pipeline.orchestrator import build_orchestrator
from app.pipeline.schemas import UserTurn

logging.basicConfig(level=logging.INFO)

CONVERSATION_TURNS = [
    "hey i m facing anxiety",
    "hi",
    "my father took loan of 1 cr and completed order of government but now they are not paying us our money is stucked and have to pay back the loan",
    "she broke up with me",
    "she cheated",
    "i m stressed",
]


async def run_baseline():
    settings = get_settings()
    orch = build_orchestrator(settings=settings)
    session_id = f"baseline_eval_{int(asyncio.get_event_loop().time())}"

    results = []

    print(f"=== Starting Baseline Evaluation for Session: {session_id} ===")
    print(f"Provider: {settings.llm_provider} | Model: {settings.openai_model}")

    for idx, message in enumerate(CONVERSATION_TURNS, 1):
        print(f"\n--- Turn {idx}: '{message}' ---")
        user_turn = UserTurn(message=message, session_id=session_id)
        res = await orch.handle_turn(user_turn)

        mem = await orch.memory.load(session_id)

        turn_data = {
            "turn_index": idx,
            "message": message,
            "emotion": {
                "dominant": res.emotion.dominant,
                "score": res.emotion.dominant_score,
                "intensity": res.emotion.intensity,
                "labels": [
                    {"emotion": item.emotion, "score": item.score}
                    for item in res.emotion.labels[:3]
                ],
            },
            "intent": {
                "label": res.intent.intent.value,
                "confidence": res.intent.confidence,
                "scores": res.intent.scores,
            },
            "route": {
                "route": res.route.route,
                "stage": res.route.stage,
                "standalone_query": res.route.standalone_query,
            },
            "citations": res.citations,
            "answer": res.answer,
            "timings": res.timings,
            "memory_summary": mem.summary,
            "recent_turns_count": len(mem.recent_turns),
        }
        results.append(turn_data)

        print(f"  Emotion: {turn_data['emotion']['dominant']} ({turn_data['emotion']['score']:.2f}, intensity={turn_data['emotion']['intensity']:.2f})")
        print(f"  Intent: {turn_data['intent']['label']} ({turn_data['intent']['confidence']:.2f})")
        print(f"  Route: {turn_data['route']['route']} ({turn_data['route']['stage']})")
        print(f"  Citations: {turn_data['citations']}")
        print(f"  Answer: {turn_data['answer']}")

    await orch.aclose()

    out_path = Path("eval/results/baseline_regression_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved baseline evaluation results to {out_path}")


if __name__ == "__main__":
    asyncio.run(run_baseline())

