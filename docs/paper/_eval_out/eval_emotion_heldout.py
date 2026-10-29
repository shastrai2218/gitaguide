# -*- coding: utf-8 -*-
"""Real, held-out evaluation of the production emotion model against the
public GoEmotions *test* split (never seen by SamLowe/roberta-base-go_emotions'
training, and not used anywhere else in this project). Standard protocol:
sigmoid score >= 0.5 per label, multi-label precision/recall/F1 per class,
plus macro and micro averages. No fabricated numbers; every row below comes
from a real forward pass of the real INT8 ONNX model used in production.
"""
import csv
import sys
import time
sys.path.insert(0, r"D:\Projects\GitaGuide\gitaguide")

from app.config import Settings
from app.pipeline.emotion import OnnxGoEmotionsBackend

EMOTIONS = [l.strip() for l in open(r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\emotions.txt", encoding="utf-8") if l.strip()]
N2I = {n: i for i, n in enumerate(EMOTIONS)}

rows = []
with open(r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\goemotions_test.tsv", encoding="utf-8") as f:
    for line in csv.reader(f, delimiter="\t"):
        if len(line) < 2:
            continue
        text, label_ids = line[0], line[1]
        gold = set(int(x) for x in label_ids.split(","))
        rows.append((text, gold))

print(f"loaded {len(rows)} held-out GoEmotions test examples", flush=True)

SAMPLE = 1500  # bounded for turnaround time; still n=1500 held-out, far above the paper's current n=0 for this model
rows = rows[:SAMPLE]

settings = Settings(mock_mode=False)
backend = OnnxGoEmotionsBackend(settings)
backend._ensure_loaded()
print(f"model loaded: {settings.emotion_onnx_repo}/{settings.emotion_onnx_file}", flush=True)

tp = [0] * len(EMOTIONS)
fp = [0] * len(EMOTIONS)
fn = [0] * len(EMOTIONS)

BATCH = 32
t0 = time.time()
for start in range(0, len(rows), BATCH):
    chunk = rows[start : start + BATCH]
    texts = [t for t, _ in chunk]
    encoded = backend._tokenizer(texts, padding=True, truncation=True, max_length=256, return_tensors="np")
    input_names = {it.name for it in backend._session.get_inputs()}
    feed = {k: v for k, v in encoded.items() if k in input_names}
    logits = backend._session.run(None, feed)[0]
    import math

    for (text, gold), row in zip(chunk, logits):
        pred = set()
        for idx, val in enumerate(row):
            score = 1.0 / (1.0 + math.exp(-float(val)))
            if score >= 0.5:
                pred.add(idx)
        for idx in range(len(EMOTIONS)):
            in_pred = idx in pred
            in_gold = idx in gold
            if in_pred and in_gold:
                tp[idx] += 1
            elif in_pred and not in_gold:
                fp[idx] += 1
            elif in_gold and not in_pred:
                fn[idx] += 1
    if start % (BATCH * 10) == 0:
        print(f"  {start}/{len(rows)}  ({time.time()-t0:.0f}s)", flush=True)

print(f"done in {time.time()-t0:.0f}s", flush=True)

precisions, recalls, f1s = [], [], []
print(f"\n{'label':<16}{'tp':>5}{'fp':>5}{'fn':>5}{'P':>7}{'R':>7}{'F1':>7}")
for i, name in enumerate(EMOTIONS):
    p = tp[i] / (tp[i] + fp[i]) if (tp[i] + fp[i]) else 0.0
    r = tp[i] / (tp[i] + fn[i]) if (tp[i] + fn[i]) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    precisions.append(p)
    recalls.append(r)
    f1s.append(f1)
    print(f"{name:<16}{tp[i]:>5}{fp[i]:>5}{fn[i]:>5}{p:>7.3f}{r:>7.3f}{f1:>7.3f}")

macro_p = sum(precisions) / len(precisions)
macro_r = sum(recalls) / len(recalls)
macro_f1 = sum(f1s) / len(f1s)
total_tp, total_fp, total_fn = sum(tp), sum(fp), sum(fn)
micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0

print(f"\nn = {len(rows)} (GoEmotions test split, held out)")
print(f"macro P={macro_p:.3f} R={macro_r:.3f} F1={macro_f1:.3f}")
print(f"micro P={micro_p:.3f} R={micro_r:.3f} F1={micro_f1:.3f}")

import json
out = {
    "provenance": {
        "model": f"{settings.emotion_onnx_repo}/{settings.emotion_onnx_file}",
        "data": "google-research/goemotions data/test.tsv, first 1,500 rows",
        "threshold": 0.5,
        "n": len(rows),
        "measured": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    },
    "labels": [
        {"label": n, "tp": tp[i], "fp": fp[i], "fn": fn[i], "support": tp[i] + fn[i],
         "precision": precisions[i], "recall": recalls[i], "f1": f1s[i]}
        for i, n in enumerate(EMOTIONS)
    ],
    "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f1},
    "micro": {"precision": micro_p, "recall": micro_r, "f1": micro_f1},
}
with open(r"D:\Projects\GitaGuide\gitaguide\eval\results\emotion_heldout_goemotions.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
