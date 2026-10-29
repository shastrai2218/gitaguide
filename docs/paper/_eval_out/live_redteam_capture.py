# -*- coding: utf-8 -*-
"""Real, live capture of full replies for the 33-prompt moral red-team set
(eval/moral_redteam.jsonl), run fresh against the deployed production API.
Captures the full reply + citations + blocked flag for each prompt so a
second, independent judge can grade them without re-querying the live
system (keeping both judges looking at the identical generated text).
"""
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://13-205-163-41.sslip.io"


def post(path, body, timeout=90):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


rows = [json.loads(l) for l in open(r"D:\Projects\GitaGuide\gitaguide\eval\moral_redteam.jsonl", encoding="utf-8") if l.strip()]
print(f"loaded {len(rows)} red-team prompts", flush=True)

results = []
t0 = time.time()
for i, row in enumerate(rows):
    sid = f"evalredteam-{uuid.uuid4().hex[:12]}"
    try:
        post("/api/consent", {"session_id": sid, "version": "beta-1"}, timeout=20)
        resp = post("/api/chat", {"session_id": sid, "message": row["message"]}, timeout=75)
        results.append({
            **row,
            "reply": resp.get("reply"),
            "blocked": resp.get("blocked"),
            "citations": [c.get("id") for c in resp.get("citations", [])],
            "intent": resp.get("intent", {}).get("label"),
            "emotion": resp.get("emotion", {}).get("label"),
        })
        print(f"[{i+1}/{len(rows)}] {row['id']:>8} cat={row['category']:<18} blocked={resp.get('blocked')!s:<6} ({time.time()-t0:.0f}s)", flush=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"[{i+1}/{len(rows)}] {row['id']} HTTPError {e.code}: {body}", flush=True)
    except Exception as e:
        print(f"[{i+1}/{len(rows)}] {row['id']} ERROR: {e}", flush=True)
    time.sleep(1.2)

with open(r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\live_redteam_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print(f"\ncompleted {len(results)}/{len(rows)} in {time.time()-t0:.0f}s", flush=True)
