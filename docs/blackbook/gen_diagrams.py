# -*- coding: utf-8 -*-
import math
from PIL import Image, ImageDraw, ImageFont

try:
    f = ImageFont.truetype("arial.ttf", 20)
    fb = ImageFont.truetype("arialbd.ttf", 20)
except Exception:
    f = ImageFont.load_default()
    fb = f

# ---------- Sequence diagram: User -> Orchestrator -> Gate -> Retriever -> LLM -> DB ----------
W, H = 1500, 620
im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
actors = ["User", "Orchestrator", "Safety Gate", "Retriever\n(Neo4j)", "LLM\n(NIM)", "PostgreSQL"]
n = len(actors)
xs = [80 + i * (W - 160) // (n - 1) for i in range(n)]
for x, name in zip(xs, actors):
    d.rectangle([x - 70, 20, x + 70, 70], outline="#1f3d66", width=2, fill="#eef3fb")
    lines = name.split("\n")
    ty = 30 if len(lines) == 1 else 22
    for l in lines:
        tw = d.textbbox((0, 0), l, font=f)[2]
        d.text((x - tw // 2, ty), l, fill="black", font=f)
        ty += 20
    d.line([x, 70, x, H - 30], fill="#999999", width=1)

def arrow(x1, y, x2, label, dashed=False):
    d.text(((x1 + x2) // 2 - len(label) * 4, y - 18), label, fill="black", font=f)
    if dashed:
        step = 10
        xx = x1
        while xx < x2 - step:
            d.line([xx, y, xx + 6, y], fill="black", width=2)
            xx += step
    else:
        d.line([x1, y, x2, y], fill="black", width=2)
    a = 0 if x2 > x1 else math.pi
    L = 10
    d.polygon([(x2, y), (x2 - L * math.cos(a - 0.4), y - L * math.sin(a - 0.4) * 0 - (0 if x2>x1 else 0)), (x2 - 10, y - 5), (x2-10, y+5)], fill="black")

y = 110
steps = [
    (0, 1, "POST /chat {message}"),
    (1, 2, "check_input(text, emotion, intent)"),
    (2, 1, "verdict: not blocked", True),
    (1, 3, "retrieve(query, emotion, intent)"),
    (3, 1, "ranked candidates + graph paths", True),
    (1, 4, "generate(prompt, candidates)"),
    (4, 1, "answer + citations", True),
    (1, 2, "check_output(answer)"),
    (2, 1, "verdict: not blocked", True),
    (1, 5, "persist turn + trace"),
    (1, 0, "ChatResponse {answer, citations, trace}", True),
]
for a, b, label, *rest in steps:
    dashed = bool(rest and rest[0])
    arrow(xs[a], y, xs[b], label, dashed)
    y += 44

d.text((60, H - 20), "Crisis path (not shown): check_input returns blocked=true at step 2; orchestrator returns the\nfixed helpline response immediately, skipping steps 4-9 entirely.", fill="#8f1f1f", font=f)
im.save("img/sequence.png")

# ---------- Data flow diagram ----------
W, H = 1500, 420
im2 = Image.new("RGB", (W, H), "white")
d2 = ImageDraw.Draw(im2)
def box(x, y, w, h, t, fill="#eef3fb", out="#1f3d66"):
    d2.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=fill, outline=out, width=2)
    lines = t.split("\n")
    th = len(lines) * 24
    ty = y + (h - th) // 2
    for l in lines:
        tw = d2.textbbox((0, 0), l, font=f)[2]
        d2.text((x + (w - tw) // 2, ty), l, fill="black", font=f)
        ty += 24
def arr(x1, y1, x2, y2):
    d2.line([x1, y1, x2, y2], fill="black", width=2)
    a = math.atan2(y2 - y1, x2 - x1)
    L = 12
    p = [(x2, y2), (x2 - L * math.cos(a - 0.4), y2 - L * math.sin(a - 0.4)), (x2 - L * math.cos(a + 0.4), y2 - L * math.sin(a + 0.4))]
    d2.polygon(p, fill="black")

box(30, 170, 160, 80, "User\nmessage")
box(240, 170, 190, 80, "Emotion +\nIntent models")
box(480, 60, 190, 80, "Safety Gate")
box(480, 280, 190, 80, "Memory\n(last 6 + summary)")
box(730, 170, 190, 80, "Router")
box(980, 60, 220, 80, "Hybrid Retriever\n(Neo4j graph)")
box(980, 280, 220, 80, "Grounded\nGenerator (NIM)")
box(1260, 170, 210, 80, "Response +\nstored trace\n(PostgreSQL)")

arr(190, 210, 240, 210)
arr(430, 210, 480, 150)   # to gate
arr(430, 210, 480, 320)   # to memory (parallel note)
arr(670, 100, 730, 190)
arr(670, 320, 730, 230)
arr(920, 210, 980, 110)
arr(920, 210, 980, 310)
arr(1200, 100, 1260, 190)
arr(1200, 320, 1260, 230)

im2.save("img/dataflow.png")
print("diagrams written")
