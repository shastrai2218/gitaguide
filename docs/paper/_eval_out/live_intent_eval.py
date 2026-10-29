# -*- coding: utf-8 -*-
"""Real, live intent-classification evaluation against the deployed production
API (https://13-205-163-41.sslip.io). Every row below is a genuine HTTP call
to the real pipeline (real NIM LLM call for intent, as in production); nothing
here is simulated or fabricated. Combines the original 40-query gold set with
the newer 60-query extension (statecraft/hinglish-heavy) for n up to 100,
each queried in its own fresh session so no conversational memory leaks
between queries.
"""
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://13-205-163-41.sslip.io"


def post(path, body, timeout=60):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_queries():
    out = []
    d = json.load(open(r"D:\Projects\GitaGuide\gitaguide\eval\gold_pairs.json", encoding="utf-8"))
    for p in d["pairs"]:
        out.append({"id": p["id"], "category": p["category"], "query": p["query"], "expected_intent": p["expected_intent"]})
    with open(r"D:\Projects\GitaGuide\gitaguide\eval\gold_queries_v2.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                j = json.loads(line)
                out.append({"id": j["id"], "category": j["category"], "query": j["query"], "expected_intent": j["expected_intent"]})
    return out


queries = load_queries()
print(f"loaded {len(queries)} queries with expected_intent", flush=True)

RESUME_FROM = 33  # already captured (by parsing the first run's log) for gp-001..gp-033
prior = json.load(open(r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\live_intent_partial.json", encoding="utf-8"))
results = [{"id": r["id"], "expected_intent": r["expected_intent"], "predicted_intent": r["predicted_intent"], "category": None, "query": None, "blocked": None} for r in prior]
errors = 0
t0 = time.time()
for i, q in enumerate(queries):
    if i < RESUME_FROM:
        continue
    sid = f"evalintent-{uuid.uuid4().hex[:12]}"
    try:
        post("/api/consent", {"session_id": sid, "version": "beta-1"}, timeout=20)
        resp = post("/api/chat", {"session_id": sid, "message": q["query"]}, timeout=75)
        intent = resp.get("intent", {}).get("label")
        blocked = resp.get("blocked")
        results.append({**q, "predicted_intent": intent, "blocked": blocked})
        print(f"[{i+1}/{len(queries)}] {q['id']:>8}  exp={q['expected_intent']:<20} got={intent!s:<20} ({time.time()-t0:.0f}s elapsed)", flush=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"[{i+1}/{len(queries)}] {q['id']} HTTPError {e.code}: {body}", flush=True)
        errors += 1
    except Exception as e:
        print(f"[{i+1}/{len(queries)}] {q['id']} ERROR: {e}", flush=True)
        errors += 1
    time.sleep(1.2)  # stay well under the production global rate limit

print(f"\ncompleted {len(results)}/{len(queries)} with {errors} errors in {time.time()-t0:.0f}s", flush=True)

with open(r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\live_intent_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

# ---- confusion matrix + per-class P/R/F1 ----
labels = sorted({r["expected_intent"] for r in results} | {r["predicted_intent"] for r in results if r["predicted_intent"]})
conf = {a: {b: 0 for b in labels} for a in labels}
for r in results:
    pred = r["predicted_intent"] or "none"
    if pred not in conf.get(r["expected_intent"], {}):
        conf.setdefault(r["expected_intent"], {})[pred] = 0
        for a in conf:
            conf[a].setdefault(pred, 0)
        labels = sorted(set(labels) | {pred})
    conf[r["expected_intent"]][pred] = conf[r["expected_intent"]].get(pred, 0) + 1

print("\nconfusion matrix (rows = expected, cols = predicted)")
header = "expected\\predicted".ljust(22) + "".join(l[:10].ljust(12) for l in labels)
print(header)
for a in labels:
    row = a.ljust(22) + "".join(str(conf.get(a, {}).get(b, 0)).ljust(12) for b in labels)
    print(row)

tp = {l: conf.get(l, {}).get(l, 0) for l in labels}
fp = {l: sum(conf.get(a, {}).get(l, 0) for a in labels if a != l) for l in labels}
fn = {l: sum(conf.get(l, {}).get(b, 0) for b in labels if b != l) for l in labels}

print(f"\n{'label':<22}{'tp':>5}{'fp':>5}{'fn':>5}{'P':>7}{'R':>7}{'F1':>7}")
f1s = []
for l in labels:
    p = tp[l] / (tp[l] + fp[l]) if (tp[l] + fp[l]) else 0.0
    r = tp[l] / (tp[l] + fn[l]) if (tp[l] + fn[l]) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    f1s.append(f1)
    print(f"{l:<22}{tp[l]:>5}{fp[l]:>5}{fn[l]:>5}{p:>7.3f}{r:>7.3f}{f1:>7.3f}")

total = len(results)
correct = sum(1 for r in results if r["predicted_intent"] == r["expected_intent"])
print(f"\noverall accuracy: {correct}/{total} = {correct/total:.3f}" if total else "no results")
print(f"macro F1: {sum(f1s)/len(f1s):.3f}" if f1s else "")
