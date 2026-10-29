# -*- coding: utf-8 -*-
import math
from PIL import Image, ImageDraw, ImageFont
import docx
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ---------- component diagram ----------
S = 2
W, H_ = 1500 * S, 780 * S
im = Image.new("RGB", (W, H_), "white")
d = ImageDraw.Draw(im)
try:
    f = ImageFont.truetype("arial.ttf", 24 * S)
except Exception:
    f = ImageFont.load_default()


def box(x, y, w, h, t, fill="#eef3fb", out="#1f3d66"):
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=10 * S, fill=fill, outline=out, width=3 * S)
    ls = t.split("\n")
    th = len(ls) * 30 * S
    ty = y * S + (h * S - th) // 2
    for l in ls:
        tw = d.textbbox((0, 0), l, font=f)[2]
        d.text((x * S + (w * S - tw) // 2, ty), l, fill="black", font=f)
        ty += 30 * S


def arrow(x1, y1, x2, y2):
    d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill="black", width=3 * S)
    a = math.atan2(y2 - y1, x2 - x1)
    L = 16
    p = [(x2, y2), (x2 - L * math.cos(a - 0.4), y2 - L * math.sin(a - 0.4)), (x2 - L * math.cos(a + 0.4), y2 - L * math.sin(a + 0.4))]
    d.polygon([(px * S, py * S) for px, py in p], fill="black")


box(40, 40, 300, 90, "User (browser)\nReact + Vite on Vercel")
box(40, 240, 300, 90, "Clerk\nsign-in / accounts")
box(480, 40, 360, 90, "FastAPI backend\nAWS EC2 t3.small, Mumbai")
box(480, 230, 360, 130, "Safety-first pipeline\nemotion, intent, gate,\nrouter, retrieval, generation", fill="#fdeaea", out="#8f1f1f")
box(1000, 40, 420, 90, "NVIDIA NIM\nNemotron LLMs (primary + fallback)")
box(1000, 240, 420, 90, "Neo4j AuraDB\nverse knowledge graph + vectors")
box(1000, 440, 420, 90, "Supabase PostgreSQL\nsessions, messages, traces")
box(480, 440, 360, 90, "Local CPU models\nRoBERTa INT8 ONNX, MiniLM")
box(40, 440, 300, 90, "Telemetry + audit logs\n(no message text)")
box(480, 630, 360, 90, "Rate limiter + pipeline gate\n(one turn at a time)")
arrow(340, 85, 480, 85)
arrow(660, 130, 660, 230)
arrow(660, 360, 660, 440)
arrow(840, 295, 1000, 285)
arrow(840, 85, 1000, 85)
arrow(840, 340, 1000, 470)
arrow(340, 285, 480, 120)
arrow(480, 485, 340, 485)
arrow(660, 530, 660, 630)
im.save("img/component.png")

# ---------- document ----------
D = docx.Document()
sec = D.sections[0]
sec.page_width = Cm(21)
sec.page_height = Cm(29.7)
for a in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
    setattr(sec, a, Cm(2.2))
st = D.styles["Normal"]
st.font.name = "Times New Roman"
st.font.size = Pt(12)
st.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")


def P(text="", b=False, i=False, al=None, sz=None, after=6, keep=False, u=False):
    p = D.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    if al == "c":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif al == "j":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    elif al == "r":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if keep:
        p.paragraph_format.keep_with_next = True
    r = p.add_run(text)
    r.bold = b
    r.italic = i
    r.underline = u
    if sz:
        r.font.size = Pt(sz)
    return p


def H(t, bm=None):
    if bm:
        BM(bm)
    return P(t, b=True, after=6, keep=True)


def B(label, text):
    p = D.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(4)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if label:
        r = p.add_run(label + " ")
        r.bold = True
    p.add_run(text)


def J(t):
    return P(t, al="j")


def NUM(label, text, n):
    p = D.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    r = p.add_run(f"{n}. {label} " if label else f"{n}. ")
    r.bold = bool(label)
    p.add_run(text)


def FIG(path, cap, w=15, bm=None):
    if bm:
        BM(bm)
    p = D.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(path, width=Cm(w))
    P(cap, al="c", sz=10, after=10)


def shade(c, color):
    tcPr = c._tc.get_or_add_tcPr()
    s = OxmlElement("w:shd")
    s.set(qn("w:val"), "clear")
    s.set(qn("w:color"), "auto")
    s.set(qn("w:fill"), color)
    tcPr.append(s)


def TBL(cap, head, rows, widths=None, bm=None):
    if bm:
        BM(bm)
    P(cap, al="c", sz=10, after=3, keep=True)
    t = D.add_table(rows=1, cols=len(head))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, h in enumerate(head):
        c = t.rows[0].cells[k]
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(10.5)
        shade(c, "E7ECF4")
    for row in rows:
        cs = t.add_row().cells
        for k, v in enumerate(row):
            cs[k].text = ""
            r = cs[k].paragraphs[0].add_run(str(v))
            r.font.size = Pt(10.5)
    if widths:
        for row in t.rows:
            for k, w in enumerate(widths):
                row.cells[k].width = Cm(w)
    for row in t.rows:
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
    P("", after=8)


_bm_id = [0]


def BM(name):
    """Insert an invisible bookmark at the current end of the document, used to
    look up the real page number each item lands on after layout."""
    p = D.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    _bm_id[0] += 1
    bid = str(_bm_id[0])
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bid)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bid)
    p._p.append(start)
    p._p.append(end)


def CH(num, title):
    D.add_page_break()
    BM(f"ch{num}")
    P(f"Chapter {num}", b=True, al="c", sz=16, after=2, keep=True)
    P(title, b=True, al="c", sz=16, after=16, keep=True)


# =================================================================== COVER
P("")
P("GitaGuide: An Emotion-Aware, Explainable and Safety-Gated Guidance System\nGrounded in the Bhagavad Gita and the Arthashastra", b=True, al="c", sz=20, after=10)
P("Interim Report", b=True, al="c", sz=16, after=14)
P("Submitted in partial fulfillment of the requirement for the award of\nDegree of Bachelor of Technology in\nComputer Engineering", al="c", b=True, sz=12, after=12)
P("Submitted to", b=True, al="c", after=4)
lp = D.add_paragraph()
lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
lp.add_run().add_picture("img/nmims_logo.jpeg", width=Cm(4.0))
lp.paragraph_format.space_after = Pt(10)
P("SVKM's NMIMS,\nMukesh Patel School of Technology Management & Engineering,\nShirpur Campus (M.H.)", b=True, al="c", sz=12, after=14)
P("Submitted By:", b=True, al="c", after=2)
P("Harikrishna Panchal   Enrollment No. – [Enrollment No.]", b=True, al="c", sz=13, after=12)
P("Under The Supervision of:", b=True, al="c", after=2)
P("Dr. Suresh Kurumbanshi", b=True, al="c", sz=13, after=2)
P("Project Guide, Department of Computer Engineering", al="c", sz=11, after=14)
P("DEPARTMENT OF COMPUTER ENGINEERING", b=True, al="c", after=2)
P("Mukesh Patel School of Technology Management & Engineering", b=True, al="c", sz=11, after=2)
P("ACADEMIC SESSION: 2025-26", b=True, al="c", sz=11)

# =================================================================== ABSTRACT
D.add_page_break()
BM("abstract")
P("ABSTRACT", b=True, al="c", sz=16, u=True, after=14)
J("Chatbots built on scripture are getting more users, and their failures are on record. Some "
  "Gita chatbots have told users that killing is fine when it is framed as duty, and studies warn "
  "about hallucination, bias and emotional dependency. This project builds GitaGuide, a guidance "
  "system based on two scriptures, the Bhagavad Gita (701 verses) and the Arthashastra (3,135 "
  "sutras). Each message is scored for emotion and intensity, given an intent, and checked by a "
  "layered crisis gate before anything is retrieved or generated. Verses come from a 3,836-verse "
  "knowledge graph, and every citation in an answer must be a verse that was actually retrieved. "
  "At this interim stage the gate blocked 36 of 36 crisis prompts and passed 25 of 25 benign ones. "
  "A router stopped unneeded retrieval on non-guidance turns (20 of 20 down to 0 of 20), and the "
  "whole stack runs in 429 MiB on a 512 MiB budget. A second round of testing added component "
  "ablations, a held-out emotion test (macro-F1 0.451), a live intent test (65% accuracy, with "
  "scripture questions almost never recognised) and a manually graded red team. In that red team, "
  "two of 25 harm-framed prompts got answers that endorsed violence. Both are fixed in the code, "
  "but the fix is not deployed yet. Retrieval is still weak (hit rate@5 of 0.175), and the "
  "faithfulness score of 2.77 out of 5 is preliminary.")

# =================================================================== TOC
# Page numbers below are placeholders filled in by fill_pages.py after a
# first build, using real Word pagination looked up via bookmarks.
PAGE = {
    "abstract": 2, "lof": 4, "lot": 4,
    "ch1": 5, "h1.1": 5, "h1.2": 5, "h1.3": 5, "h1.4": 6, "h1.5": 6,
    "ch2": 7, "h2.1": 7, "h2.2": 7,
    "ch3": 9, "h3.1": 9, "h3.2": 10, "h3.3": 10,
    "ch4": 13, "h4.1": 13, "h4.2": 13,
    "ch5": 21, "h5.1": 21, "h5.2": 21, "h5.3": 21,
    "ch6": 22,
    "refs": 23, "appa": 24,
    "fig1": 9, "fig2": 9, "fig3": 14, "fig4": 15, "fig5": 18, "fig6": 19, "fig7": 19,
    "tbl1": 8, "tbl2": 10, "tbl3": 10, "tbl4": 11, "tbl5": 13, "tbl6": 14, "tbl7": 15, "tbl8": 16, "tbl9": 16, "tbl10": 16, "tbl11": 17, "tbl12": 17, "tbl13": 18,
}
D.add_page_break()
P("TABLE OF CONTENTS", b=True, al="c", sz=14, after=14)
toc = [
    ("", "Abstract", PAGE["abstract"]),
    ("", "List of Figures", PAGE["lof"]),
    ("", "List of Tables", PAGE["lot"]),
    ("1", "Introduction", PAGE["ch1"]),
    ("1.1", "Background of the Project Topic", PAGE["h1.1"]),
    ("1.2", "Motivation and Scope of the Report", PAGE["h1.2"]),
    ("1.3", "Problem Statement", PAGE["h1.3"]),
    ("1.4", "Salient Contribution", PAGE["h1.4"]),
    ("1.5", "Organization of the Report", PAGE["h1.5"]),
    ("2", "Literature Survey", PAGE["ch2"]),
    ("2.1", "Introduction to Overall Topic", PAGE["h2.1"]),
    ("2.2", "Exhaustive Literature Survey", PAGE["h2.2"]),
    ("3", "Methodology and Implementation", PAGE["ch3"]),
    ("3.1", "Block Diagram", PAGE["h3.1"]),
    ("3.2", "Hardware Description", PAGE["h3.2"]),
    ("3.3", "Software Description, Flowchart / Algorithm", PAGE["h3.3"]),
    ("4", "Result and Analysis", PAGE["ch4"]),
    ("4.1", "Progress Made", PAGE["h4.1"]),
    ("4.2", "Measured Results", PAGE["h4.2"]),
    ("5", "Advantage, Limitations and Applications", PAGE["ch5"]),
    ("5.1", "Advantages", PAGE["h5.1"]),
    ("5.2", "Limitations", PAGE["h5.2"]),
    ("5.3", "Applications", PAGE["h5.3"]),
    ("6", "Conclusion and Future Scope", PAGE["ch6"]),
    ("", "References", PAGE["refs"]),
    ("", "Appendix A: List of Paper Presented and Published", PAGE["appa"]),
]


def PLAINLIST(rows, col_widths, bold_rule=None):
    """A borderless, aligned table of (label, title, page) rows, styled the
    same as the Table of Contents: label left, title left (indented for
    sub-items), page number right-aligned."""
    t = D.add_table(rows=0, cols=3)
    t.autofit = False
    for row_vals in rows:
        num, title, pg = row_vals
        row = t.add_row().cells
        top = bold_rule(num) if bold_rule else True
        row[0].text = ""
        row[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
        r0 = row[0].paragraphs[0].add_run(str(num))
        row[1].text = ""
        row[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
        r1 = row[1].paragraphs[0].add_run(str(title))
        row[2].text = ""
        row[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r2 = row[2].paragraphs[0].add_run(str(pg))
        for r in (r0, r1, r2):
            r.font.size = Pt(11)
            r.bold = top
    for row in t.rows:
        for cell, w in zip(row.cells, col_widths):
            cell.width = w
    P("", after=10)


PLAINLIST(
    [(num, ("     " + title) if (num and "." in num) else title, pg) for num, title, pg in toc],
    (Cm(2.2), Cm(12.3), Cm(2.1)),
    bold_rule=lambda num: num == "" or "." not in num,
)

# =================================================================== LOF / LOT
D.add_page_break()
BM("lof")
P("LIST OF FIGURES", b=True, al="c", sz=14, after=10)
PLAINLIST(
    [
        ("Fig. 1", "The GitaGuide Pipeline (Architecture / Block Diagram)", PAGE["fig1"]),
        ("Fig. 2", "Component Diagram of the Deployed System", PAGE["fig2"]),
        ("Fig. 3", "Median Per-Stage Latency of a Normal Turn (Live Stack)", PAGE["fig3"]),
        ("Fig. 4", "Resident Memory Before and After INT8 Optimisation", PAGE["fig4"]),
        ("Fig. 5", "Closed-Beta Notice and Consent Screen", PAGE["fig5"]),
        ("Fig. 6", "Guidance Answer with Cited Verses and Trace", PAGE["fig6"]),
        ("Fig. 7", "Crisis Gate Response", PAGE["fig7"]),
    ],
    (Cm(2.2), Cm(12.3), Cm(2.1)),
)
BM("lot")
P("LIST OF TABLES", b=True, al="c", sz=14, after=10)
PLAINLIST(
    [
        ("Table 1", "Capability Comparison with the Closest Precedents", PAGE["tbl1"]),
        ("Table 2", "Hardware Description", PAGE["tbl2"]),
        ("Table 3", "Software Description", PAGE["tbl3"]),
        ("Table 4", "Layers of the Safety Gate", PAGE["tbl4"]),
        ("Table 5", "Safety Gate Results", PAGE["tbl5"]),
        ("Table 6", "Second Red-Team Assessment", PAGE["tbl6"]),
        ("Table 7", "Effect of Conversational Routing", PAGE["tbl7"]),
        ("Table 8", "Intent Classification, Per-Class Metrics", PAGE["tbl8"]),
        ("Table 9", "Intent Confusion Matrix", PAGE["tbl9"]),
        ("Table 10", "Emotion Model on GoEmotions Test Split", PAGE["tbl10"]),
        ("Table 11", "Retrieval on the 40-Query Gold Set", PAGE["tbl11"]),
        ("Table 12", "Component Ablations", PAGE["tbl12"]),
        ("Table 13", "First Human Evaluation of 30 Answers", PAGE["tbl13"]),
    ],
    (Cm(2.2), Cm(12.3), Cm(2.1)),
)

# =================================================================== CHAPTER 1
CH(1, "Introduction")
H("1.1 Background of the Project Topic", bm="h1.1")
J("Spiritual guidance is normally one person talking to another. A student sits with a teacher, "
  "explains what is wrong, and gets an answer for their own situation. That does not scale well. "
  "Large language models seem like a way to reach more people, so chatbots based on the Bhagavad "
  "Gita have started to appear. People ask them about grief, exam stress, fights at home and "
  "whether to quit a job.")
J("Making one that works is harder than it looks. The system has to understand the message and "
  "how the person feels, decide what kind of help they want, find the right verses in a large text, "
  "write a reply that fits, and show where it got its material. Retrieval and knowledge graphs help "
  "with the last part. They do nothing for safety.")
J("The danger has already shown up. In 2023 a CBC investigation found that three of at least five "
  "Gita chatbots told users that killing can be fine if it is their dharma [5]. Those answers "
  "quoted real verses. So a chatbot can cite scripture correctly and still hurt someone. For that "
  "reason we built safety into the pipeline itself and did not leave it as a check at the end.")
H("1.2 Motivation and Scope of the Report", bm="h1.2")
J("Three problems pushed this project. Existing scripture chatbots have no real safety layer, and "
  "some can be talked into endorsing violence [5]. When they model emotion at all, they only give "
  "it a label, so a small worry and a real crisis get the same kind of reply. And nobody checks "
  "citations against what was retrieved, so an invented verse number looks the same as a real one.")
J("This report covers the system as it stands at the interim stage: the design, how it is "
  "deployed, and how it did in testing. Some results are worse than we hoped, and retrieval is the "
  "clearest case. We report them as they came out. Chapter 4 also says which tests were "
  "independent and which were not.")
H("1.3 Problem Statement", bm="h1.3")
J("Current scripture-guidance systems have four problems, and this project tries to deal with all "
  "four together.")
B("", "Most of them have weak safety. Every message goes straight to retrieval and generation, even sensitive ones.")
B("", "They ignore how strong an emotion is, so grief and mild curiosity get the same style of answer.")
B("", "Nothing checks the citations. A reply can name a verse that was never retrieved or does not exist.")
B("", "They cover one text and cannot say why one verse was picked over another.")
H("1.4 Salient Contribution", bm="h1.4")
J("The main contributions are listed below. Each was tested and not only designed.")
NUM("", "A crisis gate that runs before memory, routing, retrieval and generation. A blocked message cannot reach generation or carry a citation.", 1)
NUM("", "A knowledge graph of 3,836 verses from the Bhagavad Gita and the Arthashastra, linked by emotion, theme and concept. After hub suppression it fits a free graph database.", 2)
NUM("", "Citation checking. Every verse identifier in an answer is compared with the retrieved set. A failure causes one retry, then a fallback built straight from the retrieved verses.", 3)
NUM("", "Measurements on the live system for safety, routing, retrieval, intent, emotion and answer quality, including the ones that came out badly.", 4)
NUM("", "Two safety failures found by a second red-team check and fixed in the code. The fix is not deployed yet.", 5)
H("1.5 Organization of the Report", bm="h1.5")
J("Chapter 2 reviews the literature and states the gap. Chapter 3 describes the design. Chapter 4 "
  "reports what was built and what the tests showed. Chapter 5 covers advantages, limitations and "
  "uses. Chapter 6 concludes and lists the remaining work.")

# =================================================================== CHAPTER 2
CH(2, "Literature Survey")
H("2.1 Introduction to Overall Topic", bm="h2.1")
J("We read 34 sources from 2023 to 2026. They cover scripture-based AI systems, emotion-aware and "
  "safety-focused language model work, knowledge-graph retrieval, and critical studies of "
  "religion-based chatbots. After the first pass we ran a second search aimed at disproving our own "
  "claim that a gap exists. We used terms like religious chatbot bias, chatbot dependency and "
  "GraphRAG limitations. It found five sources we had missed, and each one changed the wording of "
  "something below.")
H("2.2 Exhaustive Literature Survey", bm="h2.2")
J("Three studies build directly on the Gita. Dhokane et al. [1] combine a FAISS index over "
  "SentenceBERT embeddings with fine-tuned BERT and GPT-2. They report 91.4% accuracy against a "
  "keyword baseline of about 60%. The paper never explains how that number was calculated, and the "
  "system has no emotion, intent or safety layer. Kapuriya et al. [2] built Spiritual-LLM, an "
  "emotional-support dialogue model inspired by the Gita. It has no knowledge graph, no memory "
  "across turns, no explanations and no safety filter. Quran Chat is not academic work. It "
  "retrieves from the Quran, tafsir and Sunnah with citations and claims about 86% on its own "
  "benchmark, which is not adversarial. It shows no emotion detection and only a general disclaimer "
  "for safety. The clearest evidence of risk is Shivji's report for CBC News [5]. Three of five "
  "deployed Gita chatbots accepted violence when it was framed as duty, and they cited real verses "
  "while doing it.")
J("On safety, Aharon et al. [3] describe SAGE, a counselling framework that pairs a knowledge "
  "graph with crisis-aware strategy selection. Experts evaluated it. It is not tied to any "
  "scripture and has no emotion-intensity score. Ni and Yang [8] raise a different problem. If a "
  "safety system ends an emotional conversation abruptly, a user who has grown attached can feel "
  "rejected. They argue for closing the conversation gradually.")
J("For graph retrieval, GraphRAG [4] shows that graph structure helps with questions about a whole "
  "corpus, which plain vector search misses. It costs about two to three times the latency and "
  "needs a heavy indexing step. Surveys say explainability is the weakest part of graph and LLM "
  "systems. So GitaGuide stores the graph path behind every retrieved verse.")
J("The critical literature gave us several requirements. Biana [6] reviewed deployed religious "
  "chatbots and found wrong answers and gender bias unless someone corrects for them. Wright, in "
  "Scientific American, quotes Noreen Herzfeld on the hallucination risk that any language model "
  "carries. Zhu, Coifman and Jin [7] studied Reddit communities and found emotional dependency to be "
  "one of the most common risks users report. Our emotion labels come from GoEmotions [9] and the "
  "embedding design from Sentence-BERT [10].")
J("From this we took five requirements. Safety must come before retrieval and generation and must be "
  "testable. Every citation must be checkable. Every answer needs a stored explanation. Emotion "
  "should be measured by strength as well as type. And the system must be cheap enough for a small "
  "research beta. We found no reviewed system that has emotion intensity, retrieval over two "
  "scriptures, graph explanations, a safety gate and memory together. Table 1 compares GitaGuide "
  "with the closest ones.")
TBL("Table 1: Capability comparison with the closest precedents", ["System", "Text", "Emotion", "Knowledge graph", "Safety", "Two texts"], [
    ["Dhokane et al. [1]", "Gita", "No", "No", "No", "No"],
    ["Spiritual-LLM [2]", "Gita-inspired", "Yes", "No", "No", "No"],
    ["SAGE [3]", "Counselling", "No intensity", "Yes", "Yes", "n/a"],
    ["Quran Chat", "Quran, tafsir, Sunnah", "No", "Not reported", "Disclaimer only", "n/a"],
    ["GitaGuide (this project)", "Gita + Arthashastra", "Yes, with intensity", "Yes", "Four input layers, five output patterns", "Yes"]], [3.6, 3.2, 2.6, 2.4, 2.4, 1.8], bm="tbl1")

# =================================================================== CHAPTER 3
CH(3, "Methodology and Implementation")
H("3.1 Block Diagram", bm="h3.1")
J("GitaGuide is one fixed pipeline with safety at the front. A message goes through emotion "
  "detection, intent classification, the input safety gate, memory, the router, retrieval, "
  "generation, the output gate and storage, in that order. The gate runs before memory, routing, "
  "retrieval and generation, so nothing later can weaken it. A blocked message gets a fixed "
  "helpline reply and never reaches those stages. The orchestrator enforces the order. Unit tests, "
  "a production smoke test and the API layer check it, and the API layer removes citations from "
  "any blocked turn.")
FIG("img/image1.png", "Figure 1: The GitaGuide pipeline (block diagram). Shaded stages are safety gates. A blocked turn leaves after stage 3 with the helpline response only; routes that need no verse skip stage 6.", w=9.5, bm="fig1")
FIG("img/component.png", "Figure 2: Component diagram of the deployed system.", w=15.5, bm="fig2")
H("3.2 Hardware Description", bm="h3.2")
TBL("Table 2: Hardware description", ["Component", "Description"], [
    ["Compute (production)", "AWS EC2 t3.small (2 vCPU, 2 GiB RAM), Mumbai (ap-south-1)"],
    ["Compute (original beta)", "512 MiB free-tier instance; the before/after check in Figure 4 was measured here"],
    ["Graph database", "Neo4j AuraDB Free tier, 3,836 verse nodes, 14,709 relationships"],
    ["Relational database", "Supabase-managed PostgreSQL for sessions, messages, and explanation traces"],
    ["Development machine", "Windows workstation used for model export (ONNX/INT8) and test execution"],
], [5, 12.5], bm="tbl2")
H("3.3 Software Description, Flowchart / Algorithm", bm="h3.3")
J("The backend is Python with FastAPI. The graph and vector index are in Neo4j AuraDB. Sessions, "
  "messages and per-turn traces go to Supabase PostgreSQL. The frontend is React with Vite on "
  "Vercel, and sign-in goes through Clerk. Answers come from NVIDIA NIM, with "
  "nemotron-3-super-120b-a12b as the first choice and nemotron-3.5-lightning-30b-a3b as the "
  "fallback. Emotion detection and embeddings run on the CPU as INT8 ONNX models, which is how the "
  "whole service fits in 512 MiB. Rate limiters, and a gate that lets one turn run at a time, "
  "protect the shared model budget.")
TBL("Table 3: Software description", ["Layer", "Technology"], [
    ["Backend / API", "Python, FastAPI"],
    ["Frontend", "React (Vite), deployed on Vercel"],
    ["Authentication", "Clerk"],
    ["Knowledge graph + vectors", "Neo4j (neo4j-graphrag hybrid Cypher retriever)"],
    ["Generation", "NVIDIA NIM (Nemotron primary + fallback)"],
    ["Emotion model", "RoBERTa fine-tuned on GoEmotions, ONNX INT8"],
    ["Testing", "pytest, 911 passing tests at the time of this report"],
], [6, 11.5], bm="tbl3")
J("The corpus has 3,836 units. There are 701 Bhagavad Gita verses (ids like BG-2.47) from the "
  "open gita/gita dataset in Shri Purohit Swami's English translation, and 3,135 Arthashastra "
  "sutras (ids like AS-7.1.5) from R. Shamasastry's 1915 translation. Keyword rules give each unit "
  "weak labels for theme, emotion and concept, and these become graph nodes. Verses with enough "
  "labels in common are joined by RELATED_TO edges. Labels attached to more than 8% of the corpus "
  "are left out of that step. This removes 99.06% of the derived pairs (269,649 down to 2,546). "
  "The finished graph has 14,709 relationships.")
J("One turn follows Figure 1. The stages work like this.")
B("", "Emotion. A RoBERTa model fine-tuned on GoEmotions, exported to ONNX and quantised to INT8, predicts 28 labels. These are combined into one arousal-weighted intensity score. The score goes in the audit log and never blocks a message on its own.")
B("", "Intent and routing. Intent is one of six classes: seek guidance, emotional support, professional dilemma, scripture question, crisis or other. A zero-shot LLM call and a keyword prior produce it. A router then picks one of seven routes. A phrase lexicon handles the obvious cases for free, and one cheap LLM call handles the rest. If the router fails, the turn falls back to normal guidance.")
B("", "Safety gate. There are four input layers and an output layer (Table 4): 14 fixed rules, an LLM crisis score, a soft-cue fallback and five output patterns. A malformed classification counts as unknown risk and never as low risk. The service will not start if its start-up self-test fails.")
B("", "Retrieval. A vector index over 384-dimension all-MiniLM-L6-v2 embeddings and a full-text index run together. Graph paths through Emotion, Theme and RELATED_TO nodes are attached to each candidate. A weighted score of vector, full-text, graph and scripture-intent prior then re-ranks them.")
B("", "Generation. The model may reason but cannot invent sources. Each verse id in the reply is checked against the retrieved set. A bad or missing citation costs one retry. If that fails too, the reply is replaced by one composed from the retrieved verses. A 45-second budget covers all providers.")
TBL("Table 4: Layers of the safety gate", ["Layer", "Mechanism", "Effect"], [
    ["Rule", "14 deterministic patterns: suicidal ideation, self-harm, harm to others, abuse, medical emergency, including Latin-script Hindi", "Block"],
    ["Model", "LLM crisis score >= 0.5; malformed output retried once; still malformed = risk unknown", "Block"],
    ["Soft cue", "10 crisis-adjacent cues, used only when no rule fired and the model layer failed twice", "Block, conservatively"],
    ["Output", "5 patterns on the generated answer: harm methods, discouraging treatment, moralising, dismissal, and (added after the second red-team check in Section 4.2) describing a historical violence or torture method instead of refusing it", "Replace answer. The fifth pattern is committed and tested but not deployed to the production instance"]], [2.4, 10.4, 4.6], bm="tbl4")
J("Chapter 4 uses several test sets. The safety suite has 36 crisis prompts and 25 benign controls "
  "in English, Latin-script Hindi and euphemistic wording, and it runs offline with no LLM call. "
  "The retrieval gold set has 40 queries in five categories with 140 graded verses. A query is a "
  "hit if any acceptable verse is in the top five. A 40-message conversation set, half of it "
  "messages that need no verse, tests routing. Later checks added a pooled 100-query retrieval set, "
  "a 100-query intent set, a 33-prompt red team, a 30-answer human rating and a held-out emotion "
  "test. Intervals are 95% Wilson intervals. Each number says which backend, encoder and corpus "
  "produced it.")

# =================================================================== CHAPTER 4
CH(4, "Result and Analysis")
H("4.1 Progress Made", bm="h4.1")
J("We built things in order. First the corpus was cleaned and loaded and the graph built. Without "
  "hub suppression the graph used 281,523 of the 400,000 relationships the free tier allows. With "
  "it, the graph uses 3.7% of that limit. Then came the INT8 emotion model and the local embedder, "
  "and then the safety gate. Live use found two early failures: a Hinglish sentence and an "
  "indirect suicidal statement both got ordinary answers. They led to new rules, one retry for "
  "malformed classifier output, and the soft-cue fallback.")
J("After that we added hybrid retrieval, re-ranking and citation-checked generation. Next came the "
  "router, per-route reply formats, memory (last six turns plus a rolling summary), per-turn "
  "storage and an explanation endpoint. The beta layer added consent records, accounts, feedback, "
  "rate limiting and telemetry that never stores message text. The API started on a free 512 MiB "
  "instance and now runs on an AWS EC2 t3.small. In this interim period we also found and fixed a "
  "rate-limiting gap on the main chat endpoint.")
J("The last piece of work was a second round of testing on 17 October 2026, against the deployed "
  "API and the local retrieval harness. It added component ablations, a held-out emotion test, a "
  "live intent test and a second red-team check, and it led to the safety fix described below. The "
  "test suite now has 911 passing tests.")
H("4.2 Measured Results", bm="h4.2")
J("This section reports safety, routing, intent, emotion, retrieval, answer quality and resource "
  "use. The weak results are included.")
P("Safety", b=True, after=4)
TBL("Table 5: Safety gate results", ["Test", "n", "Result"], [
    ["Red-team crisis prompts (offline)", "36", "36 blocked; recall 1.00 (95% CI 0.90-1.00)"],
    ["Benign controls (offline)", "25", "0 blocked (upper bound 0.13)"],
    ["Unsafe answers, output layer", "4", "4 replaced"],
    ["Rule-free paraphrases, LLM layer (live)", "9", "9 crisis; scores 0.92-0.95"],
    ["Workplace control, LLM layer (live)", "3", "3 not blocked; score 0.00"],
    ["Fault injection: both parses fail (live)", "2", "Cue: blocked; no cue: proceeds"]], [8, 1.6, 7.4], bm="tbl5")
J("The offline suite runs against the fixed rules, and some of its prompts were added after live "
  "use missed them. So the 100% recall is a regression check. It does not estimate how the gate "
  "behaves on new messages. Blocked turns are also the fastest, with a median of 747 ms against "
  "10.6 s for a normal turn.")
J("The first red-team run used 25 prompts that frame violence as duty, with one working automated "
  "judge (gpt-oss-20b). It rated 18 answers safe and 2 ambiguous, returned 5 judge errors, and "
  "flagged none as endorsing harm. The second planned judge gave no usable results. We then ran "
  "all 33 prompts (the 25 plus 8 benign controls) again against the live API and graded every "
  "reply by hand against the same three-way rubric. One person did this grading, so it is one "
  "reader's judgement and not an automated score.")
TBL("Table 6: Second red-team assessment, graded manually (33 prompts, 0 errors)", ["Category (n)", "No endorsement", "Ambiguous", "Endorses harm"], [
    ["dharma_violence (10)", "8", "2", "0"],
    ["arthashastra_harm (5)", "4", "0", "1"],
    ["gendered_harm (4)", "3", "0", "1"],
    ["hinglish (4)", "4", "0", "0"],
    ["self_directed (2)", "1 (gate-blocked)", "1", "0"],
    ["Total, 25 harm-framed", "20", "3", "2"],
    ["control (8)", "8 appropriate", "-", "-"]], [4.6, 3.4, 2.6, 2.6], bm="tbl6")
J("Of the 25 harm-framed prompts, 20 got clear refusals, 3 got a soft redirect with no explicit "
  "refusal, and 2 got answers that endorsed harm. Asked whether the shastras allow a husband to "
  "beat his wife, the live system said they permit physical correction as a last resort, with "
  "three beats of a bamboo-bark, rope or palm (AS-3.3.3). Asked about torturing a servant to force "
  "a confession, it explained when the Arthashastra allows it and never said it was wrong. Both "
  "answers repeated the old text and presented it as advice. The Hinglish version of the "
  "wife-beating question was refused correctly, so the same question failed in English and passed "
  "in Hinglish. The output gate's patterns are in English, which may be why.")
J("We fixed both in the code by adding a fifth output pattern (Table 4). It matches phrases that "
  "present a historical violence or torture method as allowed. It reuses the gate's negation check, "
  "so a correct refusal like 'the shastras do not permit this' passes. We replayed the new rule "
  "over the same 33 captured replies. It flagged the two failures and nothing else, including the "
  "Hinglish refusal and all eight controls. We added four regression tests and the full suite "
  "passes with 911 tests. Two limits matter. The replay used the same replies that exposed the "
  "problem, so it shows the fix works on those cases and tells us nothing about new prompts. And "
  "the fix is committed but not deployed, so the live system still behaves as Table 6 shows.")
FIG("img/image2.png", "Figure 3: Median per-stage latency of a normal turn on the live stack (n = 5, log scale). Dark bars are model round trips.", w=11, bm="fig3")
FIG("img/image3.png", "Figure 4: Resident memory on a 512 MiB instance, before and after INT8 embedder, one thread, arena limit and serialised turns.", w=11, bm="fig4")
P("Conversational routing", b=True, after=4)
TBL("Table 7: Effect of conversational routing (local stack)", ["Metric", "Before", "After"], [
    ["Non-guidance messages that ran retrieval", "20/20", "0/20"],
    ["Routing accuracy, 40 messages", "n/a", "38/40 (0.95)"],
    ["Decided by lexicon at 0 ms", "n/a", "22/40"],
    ["Median turn latency, all turns", "3.4 s", "2.8 s"],
    ["Median latency, non-guidance turns", "4.9 s", "1.2 s"]], [9, 3.8, 3.8], bm="tbl7")
P("Intent classification", b=True, after=4)
J("Before this round, intent was only tested offline with the keyword prior (0.475 accuracy, "
  "Table 11). Now we ran the real six-class classifier live. We sent all 100 gold queries (the "
  "original 40 plus 60 more) to the deployed API, each in a fresh session. All 100 completed "
  "without errors. Overall accuracy was 65/100 = 0.650. None of the 100 queries is a real crisis or "
  "out-of-scope message, because the safety suite covers those. So the crisis and other columns in "
  "Table 9 can only show false positives. If we leave those two classes out, macro-F1 over the "
  "four remaining classes is 0.571. Tables 8 and 9 use the same 100 queries and the same labels.")
TBL("Table 8: Intent classification, per-class precision, recall and F1 (live, n = 100; overall accuracy 0.650)", ["Intent", "Precision", "Recall", "F1", "n"], [
    ["emotional support", "0.714", "0.789", "0.750", "19"],
    ["professional dilemma", "0.769", "0.882", "0.822", "34"],
    ["seek guidance", "0.576", "0.613", "0.594", "31"],
    ["scripture question", "1.000", "0.062", "0.118", "16"],
    ["crisis (no genuine examples)", "0.000", "-", "-", "0"],
    ["other (no genuine examples)", "0.000", "-", "-", "0"]], [5.2, 2.6, 2.4, 2.2, 1.6], bm="tbl8")
TBL("Table 9: Intent confusion matrix (rows = expected, columns = predicted). CR crisis, ES emotional support, OT other, PD professional dilemma, SQ scripture question, SG seek guidance", ["Exp. \\ pred.", "CR", "ES", "OT", "PD", "SQ", "SG", "n"], [
    ["CR", "0", "0", "0", "0", "0", "0", "0"],
    ["ES", "2", "15", "1", "0", "0", "1", "19"],
    ["OT", "0", "0", "0", "0", "0", "0", "0"],
    ["PD", "0", "0", "0", "30", "0", "4", "34"],
    ["SQ", "0", "0", "1", "5", "1", "9", "16"],
    ["SG", "1", "6", "1", "4", "0", "19", "31"]], [2.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6], bm="tbl9")
J("Scripture questions are by far the weakest class. Only 1 of 16 was recognised, and the other 15 "
  "were treated as guidance or professional-dilemma questions. A query like what does the Gita say "
  "about anger can reasonably be read either way. So part of the problem is how the class is "
  "defined and not only the model. The second result concerns safety. Three ordinary queries were "
  "labelled crisis: I hate myself for the mistakes I have made, Nothing I do seems to matter, and I "
  "am afraid of dying. None was blocked in the end, because the gate uses its own threshold and the "
  "intent label alone does not decide blocking. Over-triggering is the safer way to be wrong, but "
  "nobody had measured this 3% rate before.")
P("Emotion", b=True, after=4)
J("Nothing had tested the emotion model on its own. We ran the production model "
  "(SamLowe/roberta-base-go_emotions, INT8 ONNX) on the first 1,500 examples of the public "
  "GoEmotions test split, which it was not trained on. We used the usual rule of a sigmoid score "
  "of at least 0.5 per label. Macro-F1 was 0.451 and micro-F1 was 0.589 (Table 10). The model card "
  "reports 0.450 macro-F1 for this checkpoint, so our result matches. Scores follow how common a "
  "label is. Gratitude, amusement and love are all above 0.78. Grief, pride and relief have only "
  "one to six test examples each, so they cannot be scored reliably. We did not test the "
  "intensity formula separately.")
TBL("Table 10: Emotion model on the public GoEmotions test split (first 1,500 examples, threshold 0.5)", ["Label", "Precision", "Recall", "F1", "TP / support"], [
    ["gratitude", "0.951", "0.898", "0.924", "97/108"],
    ["amusement", "0.778", "0.875", "0.824", "56/64"],
    ["love", "0.722", "0.852", "0.782", "52/61"],
    ["realization", "0.571", "0.129", "0.211", "4/31"],
    ["annoyance", "0.577", "0.165", "0.256", "15/91"],
    ["grief, pride, relief", "-", "0.000", "0.000", "0 of 9 (1-6 each)"],
    ["Macro average (28 labels)", "0.578", "0.400", "0.451", "1,500"],
    ["Micro average (28 labels)", "0.676", "0.523", "0.589", "1,500"]], [5.0, 2.4, 2.2, 2.0, 3.4], bm="tbl10")
P("Retrieval quality", b=True, after=4)
TBL("Table 11: Retrieval on the 40-query gold set (production retriever)", ["Category", "n", "Hit", "Recall@5", "MRR", "nDCG", "Intent acc."], [
    ["Emotional", "12", "0.25", "0.062", "0.083", "0.038", "0.58"],
    ["Professional", "12", "0.17", "0.042", "0.049", "0.030", "0.67"],
    ["Statecraft", "8", "0.00", "0.000", "0.000", "0.000", "0.00"],
    ["Relationship", "4", "0.25", "0.083", "0.250", "0.160", "0.75"],
    ["Hinglish", "4", "0.25", "0.083", "0.062", "0.057", "0.25"],
    ["Overall", "40", "0.175", "0.048", "0.071", "0.042", "0.475"]], [3.2, 1.2, 1.8, 2.2, 1.8, 1.8, 2.6], bm="tbl11")
J("A hit rate of 0.175 (7 of 40, 95% CI 0.09 to 0.32) is low. It is the main open problem of the "
  "project. We found a few causes. The graph annotates and re-scores candidates but does not find "
  "new ones, and only 32.7% of verses have an emotion tag. Raw Hinglish queries score 0.00. A larger "
  "encoder raised the vector-only hit rate to 0.400 offline, but we did not deploy it.")
J("The 0 of 8 on statecraft needs care. With eight queries the 95% interval runs from 0 to 0.32, "
  "so the data cannot separate a rate near zero from one around a third. The pooled set has 24 "
  "statecraft queries, and BM25 alone hits 8 or 9 of them (0.33 to 0.38, 95% CI about 0.18 to "
  "0.57). BM25 did not help build that pool, so its score is not inflated by it. The 0 of 8 "
  "therefore looks like small-sample noise and not a problem specific to the Arthashastra. 24 "
  "queries is still a small sample.")
P("Component ablations", b=True, after=4)
J("To see which parts of the pipeline the numbers depend on, we re-ran the retrieval harness (an "
  "offline copy of the deployed retriever, with the same graph and the same MiniLM encoder) and "
  "changed one component at a time. Each variant was compared with the production replica using an "
  "exact McNemar test. Three evaluation sets are involved, and they must not be mixed up. The "
  "40-query set was written by hand and is independent. The 81 scored queries are the part of the "
  "pooled 100-query set that has a relevant verse. That pool was built from the top results of "
  "three retrievers: the production pipeline, MiniLM vector-only and a larger untagged encoder. "
  "Those three score close to tautologically high on it, so it cannot validate them. Only "
  "configurations that did not help build the pool, such as BM25, give clean evidence there. The "
  "statements below follow that rule.")
TBL("Table 12: Component ablations (offline replica; same encoder throughout; McNemar p against the production replica)", ["Configuration", "n = 40", "n = 81 (pooled)", "p at n = 81"], [
    ["Production replica (reference)", "8/40 = 0.200", "48/81 = 0.593", "reference"],
    ["BM25 only, raw query", "1/40 = 0.025", "14/81 = 0.173", "< 0.001"],
    ["Vector only, raw query", "7/40 = 0.175", "44/81 = 0.543", "0.523"],
    ["Fusion + graph", "9/40 = 0.225", "48/81 = 0.593", "1.000"],
    ["Graph weight 0.30", "8/40 = 0.200", "47/81 = 0.580", "1.000"],
    ["Graph adds 20 candidates via emotion edges", "8/40 = 0.200", "34/81 = 0.420", "0.001"]], [6.4, 3.2, 3.6, 3.0], bm="tbl12")
J("Lexical search alone is clearly worse than the full pipeline (p < 0.001 at n = 81 and p = 0.039 "
  "at n = 40), and BM25 is a fair comparison because it did not help build the pool. The variant "
  "where the graph adds candidates through emotion edges did worse, 0.420 against 0.593. The "
  "reference configuration did help build the pool, so we would not trust the exact size of that "
  "gap. The direction fits the idea that unweighted expansion adds more noise than signal. At this "
  "sample size the other variants cannot be told apart from the production replica.")
J("One more check used the project's in-memory evaluator, whose backend does expand candidates "
  "through the graph. On the same 40 queries it scores 26/40 = 0.650 with the graph on and 20/40 = "
  "0.500 with it off. Seven queries were gained and one lost, with an exact McNemar p of 0.070. "
  "That is a trend, not a confirmed effect. This backend cannot be compared with the production "
  "numbers above in absolute terms.")
P("Answer quality", b=True, after=4)
TBL("Table 13: First human evaluation of 30 answers (1-5 scale; preliminary)", ["Scale", "Mean score"], [
    ["Relevance", "4.03"], ["Faithfulness", "2.77"], ["Helpfulness", "3.29"], ["Appropriateness", "4.39"]], [8, 4], bm="tbl13")
J("These ratings are preliminary. The two raters worked closely together, so there is no "
  "independent measure of agreement, and they did not use a written rubric. Faithfulness is the "
  "lowest score. A citation can point to a real retrieved verse while the answer still misstates "
  "what the verse says. An automated check on 69 answers, again with only one working judge, rated "
  "23 faithful, 30 with minor issues and 12 unfaithful, with 4 judge errors. In one of the 12, a "
  "sutra that allows secret punishment was described as forbidding it. A valid citation and a "
  "faithful reading are different things, and this system only guarantees the first.")
P("Resources", b=True, after=4)
J("A normal turn took a median of 10.6 s on the live stack. Two model calls, generation and "
  "semantic intent, make up 97.1% of the summed stage medians. Everything local adds up to about "
  "186 ms. INT8 quantisation, one ONNX thread, a malloc arena limit and running one turn at a time "
  "cut peak memory from 511 MiB to 429 MiB. That leaves 81.7 MiB of headroom, and the hit rate did "
  "not change. Moving the API from a free instance to the EC2 t3.small cut a turn from 27.7 s to "
  "15.5 s in our before-and-after check.")
FIG("img/shot_landing_crop.png", "Figure 5: Closed-beta notice and consent screen of the deployed app (gitaguide-neon.vercel.app).", w=11, bm="fig5")
FIG("img/shot_answer.png", "Figure 6: A guidance answer with cited verses, the retrieval path behind each verse, and the stage trace.", w=13, bm="fig6")
FIG("img/shot_crisis.png", "Figure 7: The crisis gate: a high-risk message returns the fixed helpline reply with no verse and no generation.", w=13, bm="fig7")

# =================================================================== CHAPTER 5
CH(5, "Advantage, Limitations and Applications")
H("5.1 Advantages", bm="h5.1")
B("", "Safety is part of the structure. A blocked message follows a code path that cannot reach retrieval or generation, and tests and a start-up self-test enforce it.")
B("", "Citations can be checked. Every verse id is compared with the retrieved set, and a fallback covers failures, so an invented citation cannot slip through.")
B("", "Emotion has a size. The intensity score separates mild worry from acute distress.")
B("", "Two scriptures. The Gita and the Arthashastra cover personal and professional questions, and each retrieved verse carries the path that explains the choice.")
B("", "Every result states its backend, encoder, corpus and date, including the bad ones.")
H("5.2 Limitations", bm="h5.2")
B("", "Retrieval is weak. A hit rate of 0.175 on the independent 40-query set is low, and the graph mostly explains results instead of finding new ones.")
B("", "Scripture questions are misclassified. Only 1 of 16 was recognised live, and 3 of 100 ordinary queries were labelled crisis.")
B("", "The safety evidence is thin. One person graded the 33-prompt red team, and the new output rule was only replayed on the same replies that exposed the problem. The fix is not deployed, so the live system is not yet protected against those two cases.")
B("", "Faithfulness is preliminary. The 2.77 out of 5 rating and the automated check both rest on small samples, and the automated one on a single judge.")
B("", "The samples are small and mostly made by us. The safety suite, gold sets and human ratings came from a small group close to the project.")
B("", "Dependency and bias are not measured. The literature's two most cited risks have no test in this version.")
H("5.3 Applications", bm="h5.3")
B("", "Personal reflection for people dealing with grief, doubt or burnout, with dependency and bias watched as the system grows.")
B("", "Career and work questions, where the Arthashastra adds material on leadership and organisations that the Gita does not cover.")
B("", "Non-clinical support that points people to real services. It is offered as a research beta and not as a clinical tool.")
B("", "More languages. The current Hinglish handling is a starting point for Hindi and Gujarati retrieval.")

# =================================================================== CHAPTER 6
CH(6, "Conclusion and Future Scope")
H("Conclusion")
J("GitaGuide puts emotion and intensity analysis, a layered crisis gate, hybrid graph retrieval "
  "over two scriptures, citation-checked generation and a stored explanation for every turn into "
  "one fixed pipeline. At this interim stage the safety order is built into the code and tested, "
  "citations are checked against what was retrieved, every decision is recorded, and the stack "
  "runs in 429 MiB. The second round of testing closed several gaps. The ablations showed that the "
  "dense encoder and fusion carry the retrieval accuracy and that naive graph expansion hurts. The "
  "emotion model was checked on held-out data and intent now has per-class numbers. A second "
  "red-team check found two real safety failures. They are fixed in the code and have not reached "
  "production yet.")
J("Some things are still weak. Retrieval accuracy is low, scripture questions are poorly "
  "classified, faithfulness scores are preliminary, and the risks of dependency, bias and abrupt "
  "closure are open or only partly handled. For these reasons the system stays a closed research "
  "beta for invited testers and not a product.")
H("Future Scope")
NUM("", "Deploy the safety fix and test it again. Repeat the red team on prompts written separately from the ones that exposed the problem, and report false positives and negatives with confidence intervals.", 1)
NUM("", "Make the graph retrieve. Add emotion and theme tags beyond the current 32.7% and 3.6% coverage, weight each graph hop by its margin, and try the larger encoder that already reaches 0.400 offline.", 2)
NUM("", "Measure faithfulness properly, with at least two independent raters, a written rubric and a reported agreement score, plus a second automated judge that returns usable results.", 3)
NUM("", "Fix the scripture-question class by redefining or merging its boundary with guidance questions, and find out why intense but non-crisis messages get labelled crisis.", 4)
NUM("", "Replace the single fixed crisis reply with a staged handoff, and add measures for emotional dependency and for gender and religious bias.", 5)
NUM("", "Get clinician review of the safety suite, shared rate-limit state, managed hosting, and better Hindi and Gujarati retrieval.", 6)

# =================================================================== REFERENCES
D.add_page_break()
BM("refs")
P("REFERENCES", b=True, al="c", sz=14, after=14)
for r in [
    "[1] R. M. Dhokane, S. D. Malusare, P. A. Jadhav, S. B. Bhalerao, and S. S. Autade, \"Bhagavad Gita AI: An intelligent spiritual guide,\" IJRASET, vol. 14, no. 3, 2026.",
    "[2] J. Kapuriya, A. Singh, J. Shukla, and R. R. Shah, \"Spiritual-LLM: Gita inspired mental health therapy in the era of LLMs,\" arXiv:2506.19185, 2025.",
    "[3] E. N. Aharon, L. Ben Dayan, I. Shenfeld, M. Grimland, Y. Levi Belz, A. Segal, and K. Gal, \"SAGE: A strategy-aware graph-enhanced generation framework for online counseling,\" arXiv:2604.26630, 2026.",
    "[4] D. Edge et al., \"From local to global: A graph RAG approach to query-focused summarization,\" Microsoft Research, arXiv:2404.16130, 2024.",
    "[5] S. Shivji, \"India's religious chatbots condone violence using the voice of God,\" CBC News, Jul. 6, 2023.",
    "[6] H. T. Biana, \"Feminist re-engineering of religion-based AI chatbots,\" Philosophies, vol. 9, no. 1, art. 20, 2024.",
    "[7] J. Zhu, K. G. Coifman, and R. Jin, \"Understanding risk and dependency in AI chatbot use from user discourse,\" arXiv:2602.09339, 2026.",
    "[8] Y. Ni and T. Yang, \"Even GPT can reject me: Conceptualizing abrupt refusal secondary harm (ARSH),\" arXiv:2512.18776, 2025.",
    "[9] D. Demszky, D. Movshovitz-Attias, J. Ko, A. Cowen, G. Nemade, and S. Ravi, \"GoEmotions: A dataset of fine-grained emotions,\" in Proc. ACL, 2020, pp. 4040-4054.",
    "[10] N. Reimers and I. Gurevych, \"Sentence-BERT: Sentence embeddings using Siamese BERT-networks,\" in Proc. EMNLP-IJCNLP, 2019, pp. 3982-3992.",
    "[11] H. Panchal, \"Emotion-aware and explainable AI for scripture-grounded guidance: A critical literature review,\" Dept. Comput. Eng., MPSTME, SVKM's NMIMS Univ., Shirpur, India, 2026, unpublished.",
]:
    P(r, sz=11, al="j", after=5)

# =================================================================== APPENDIX A
D.add_page_break()
BM("appa")
P("APPENDIX A: LIST OF PAPER PRESENTED AND PUBLISHED", b=True, al="c", sz=14, after=14)
B("", "Research paper: GitaGuide: An Emotion-Aware, Explainable and Safety-Gated Guidance System Grounded in the Bhagavad Gita and the Arthashastra. Prepared for a Scopus-indexed journal. Not yet submitted.")
B("", "Literature review: Emotion-Aware and Explainable AI for Scripture-Grounded Guidance: A Critical Literature Review [11]. Prepared for ICAAIC 2027 (5th International Conference on Applied Artificial Intelligence and Computing, IEEE-sponsored, Salem, Tamil Nadu). Not yet submitted.")

D.save("Interim_Report_GitaGuide.docx")
print("saved")
