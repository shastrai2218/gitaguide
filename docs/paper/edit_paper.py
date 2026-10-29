# -*- coding: utf-8 -*-
"""Text-only revision pass addressing external reviewer feedback.
No new experiments are run or numbers invented; this only tightens
framing, strengthens the stated limitations, and turns vague future-work
items into specific, scoped commitments that match the reviewer's five
priorities."""
import docx
from docx.shared import Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

SRC = "Research_Paper.docx"
D = docx.Document(SRC)


def set_single_run(p, text):
    """Replace a paragraph's content with one plain run, dropping any
    existing runs but keeping the paragraph's own formatting (alignment,
    style) untouched. Used only on plain body paragraphs with no internal
    bold/italic spans."""
    for r in list(p.runs):
        r.text = ""
    p.runs[0].text = text
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)


def append_to_run(p, suffix, run_index=-1):
    p.runs[run_index].text += suffix


def find(substr, start=0):
    for i in range(start, len(D.paragraphs)):
        if substr in D.paragraphs[i].text:
            return i
    raise ValueError(f"not found: {substr!r}")


def insert_paragraph_after(p, text, italic_lead=None):
    new_p = OxmlElement("w:p")
    p._p.addnext(new_p)
    from docx.text.paragraph import Paragraph
    np = Paragraph(new_p, p._parent)
    if italic_lead:
        r0 = np.add_run(italic_lead)
        r0.italic = True
        np.add_run(text)
    else:
        np.add_run(text)
    return np


# ------------------------------------------------------------------ 1. Abstract
i = find("We report five measurement pitfalls that would otherwise have passed unnoticed.")
p = D.paragraphs[i]
append_to_run(
    p,
    " None of the five validation gaps a reviewer would reasonably ask for next — "
    "independent retrieval ablations, multi-annotator faithfulness scoring, an "
    "independently built red team, component ablations, and per-class intent/emotion "
    "metrics — has been closed yet; Section VI-C states each as a scoped, specific "
    "next step rather than a general promise.",
)

# ------------------------------------------------------------------ 2. Annotator protocol note after "Instruments."
i = find("Instruments. The safety suite has 36 crisis prompts")
p = D.paragraphs[i]
insert_paragraph_after(
    p,
    "Annotator protocol. The 30-answer and 100-query human judgements (Section V-G) "
    "used a shared 1–5 (answers) or 0–2 (retrieval relevance) scale discussed verbally "
    "between the two raters before scoring, not a written, independently piloted rubric, "
    "and inter-annotator agreement (e.g. Cohen's κ) was not computed; one disagreement "
    "in the 1,296-judgement retrieval pool was adjudicated by discussion. These numbers are "
    "therefore reported as a first, consensus signal, not a validated instrument, and a "
    "written rubric with independent, unbriefed annotators is the first item of planned "
    "work in Section VI-C.",
    italic_lead=None,
)

# ------------------------------------------------------------------ 3. VI-A: ablation caveat
i = find("query language handled upstream of the encoder.")
p = D.paragraphs[i]
append_to_run(
    p,
    " These architectural choices have not yet been isolated by ablation: the numbers "
    "in Section V describe the full pipeline, not the marginal contribution of the "
    "knowledge graph, the safety gate, or emotion-aware routing taken alone (Section VI-C).",
)

# ------------------------------------------------------------------ 4. Table VIII: two new rows
t = D.tables[7]
new_rows = [
    (
        "Individual components (graph, gate, emotion routing) each add value",
        "Not measured; reported numbers are for the full pipeline only",
        "No ablation yet (Section VI-C, item iv)",
    ),
    (
        "Intent and emotion classification are accurate",
        "Intent acc. 0.475 overall (Table VI); emotion model runs in production",
        "No per-class precision/recall, no confusion matrix, no held-out "
        "emotion test set (Section VI-C, item v)",
    ),
]
for claim, evidence, limit in new_rows:
    row = t.add_row()
    for cell, val in zip(row.cells, (claim, evidence, limit)):
        cell.text = ""
        r = cell.paragraphs[0].add_run(val)
        r.font.size = Pt(8)

# ------------------------------------------------------------------ 5. Threats to validity: concrete commitments
i = find("Threats to validity. The gold set was written by the author")
p = D.paragraphs[i]
append_to_run(
    p,
    " None of these samples has yet been re-scored by an annotator who did not also "
    "design the test; that is the single change most likely to move every number in "
    "Section V, and it precedes any of the component work below.",
)

# ------------------------------------------------------------------ 6. Rewritten, prioritized future-work paragraph
i = find("Future work, in order:")
p = D.paragraphs[i]
set_single_run(
    p,
    "Future work, in priority order, before any claim of validated accuracy: "
    "(i) retrieval — build a manually verified, multi-annotator gold set; compare "
    "BM25, dense vector, hybrid and graph-assisted retrieval on identical queries; and "
    "report hit rate, Recall@5, MRR and nDCG@5 broken down by scripture, without tuning "
    "against the test set; "
    "(ii) faithfulness — have at least two independent annotators score answers against "
    "cited passages on a written rubric (factual correctness, source entailment, citation "
    "accuracy, harmful interpretation), measure inter-annotator agreement, and test whether "
    "stricter prompting, re-ranking or the deterministic fallback improves the score; "
    "(iii) safety — repeat the red team with an independently constructed prompt set "
    "(indirect, ambiguous, misspelled and multilingual phrasings), report false positives "
    "and negatives with confidence intervals, repeat fault injection, and add independent "
    "human review rather than a single LLM judge; "
    "(iv) ablations — measure vector-only vs. hybrid vs. graph-assisted retrieval, "
    "emotion-aware vs. emotion-blind routing, the gate enabled vs. disabled offline, and "
    "citation-constrained generation vs. the deterministic fallback, to show which "
    "components the reported numbers actually depend on; "
    "(v) intent and emotion — build a labelled evaluation set with explicit class "
    "definitions, report per-class precision, recall, F1 and a confusion matrix, and "
    "evaluate emotion and intensity separately from whether the pipeline runs; and "
    "(vi) extend the graph architecture to further texts and to Hindi and Gujarati "
    "retrieval, and study whether shorter answers help users. A repeatable, versioned "
    "evaluation configuration (model, encoder and corpus snapshot, gold sets and scripts) "
    "will accompany each of these, consistent with the data and code availability "
    "statement below.",
)

D.save(SRC)
print("revised")
