# -*- coding: utf-8 -*-
"""Second revision pass: incorporates REAL, newly measured results obtained
by re-running the project's own evaluation harnesses (eval/baselines.py,
eval/retrieval_eval.py, a held-out GoEmotions pass, and live calls against
the deployed production API) on 2026-10-17. Every number here was produced
by an actual run recorded under docs/paper/_eval_out/; none is invented.
"""
import json
import docx
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph

import sys as _sys
SRC = r"D:\Projects\GitaGuide\gitaguide\docs\paper\Research_Paper.docx"
if "--real" not in _sys.argv:
    SRC_OUT = r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out\_staged.docx"
else:
    SRC_OUT = SRC
OUT = r"D:\Projects\GitaGuide\gitaguide\docs\paper\_eval_out"
D = docx.Document(SRC)


def find(substr, start=0):
    for i in range(start, len(D.paragraphs)):
        if substr in D.paragraphs[i].text:
            return i
    raise ValueError(f"not found: {substr!r}")


def append_to_run(p, suffix, run_index=-1):
    p.runs[run_index].text += suffix


def replace_text(p, old, new):
    """Replace ``old`` with ``new`` inside whichever single run contains it,
    preserving every run's own formatting (bold/italic) rather than
    collapsing the whole paragraph into run 0's style."""
    for r in p.runs:
        if old in r.text:
            r.text = r.text.replace(old, new)
            return
    full = "".join(r.text for r in p.runs)
    raise AssertionError(
        f"{old!r} not found within any single run (spans multiple runs?); "
        f"paragraph text: {full!r}"
    )


def shade(c, color):
    tcPr = c._tc.get_or_add_tcPr()
    s = OxmlElement("w:shd")
    s.set(qn("w:val"), "clear")
    s.set(qn("w:color"), "auto")
    s.set(qn("w:fill"), color)
    tcPr.append(s)


def para_after(anchor_p, text=None, bold_lead=None, align=None):
    """Insert a new paragraph right after ``anchor_p`` (a Paragraph)."""
    new_p = OxmlElement("w:p")
    anchor_p._p.addnext(new_p)
    np = Paragraph(new_p, anchor_p._parent)
    if align:
        np.alignment = align
    if bold_lead:
        r0 = np.add_run(bold_lead + " ")
        r0.bold = True
    if text:
        np.add_run(text)
    return np


def para_after_element(element, parent):
    new_p = OxmlElement("w:p")
    element.addnext(new_p)
    return Paragraph(new_p, parent)


import copy
from docx.table import Table


def table_after(anchor_p, caption, head, rows, template_table_index=2, col_widths=None, font_pt=8):
    """Clone an existing in-document table (same visual style as every other
    table in this IEEE-formatted paper) and relocate it right after
    ``anchor_p``, then overwrite its cell text. Cloning an existing table's
    XML, rather than building one from python-docx defaults, keeps the new
    table visually identical to Tables I-IX (this docx defines no named
    "Table Grid" style; the existing look comes from explicit per-cell
    borders baked into each table's XML)."""
    cap_p = para_after(anchor_p, None, align=WD_ALIGN_PARAGRAPH.CENTER)
    r = cap_p.add_run(caption)
    r.font.size = Pt(9)

    template = D.tables[template_table_index]
    new_tbl_element = copy.deepcopy(template._tbl)
    cap_p._p.addnext(new_tbl_element)
    t = Table(new_tbl_element, cap_p._parent)

    # Resize to the wanted shape: trim/extend columns, then rows.
    while len(t.columns) > len(head):
        t._tbl.remove(t._tbl.tblGrid.tblGrid_lst[-1]) if False else None
        break  # column removal via raw XML is fragile; instead rebuild below
    # Simplest robust approach: clear all rows, keep row 0 as header template,
    # then add_row() for each data row (add_row reuses row 0's cell styling).
    while len(t.rows) > 1:
        t._tbl.remove(t.rows[-1]._tr)
    # Fix header cell count by cloning/trimming the last cell of row 0.
    hdr_row = t.rows[0]
    while len(hdr_row.cells) < len(head):
        last_tc = hdr_row.cells[-1]._tc
        new_tc = copy.deepcopy(last_tc)
        last_tc.addnext(new_tc)
    while len(hdr_row.cells) > len(head):
        t._tbl.rows[0]._tr.remove(hdr_row.cells[-1]._tc)

    for k, htext in enumerate(head):
        c = hdr_row.cells[k]
        for para in c.paragraphs:
            for rn in list(para.runs):
                rn.text = ""
        if not hdr_row.cells[k].paragraphs[0].runs:
            hdr_row.cells[k].paragraphs[0].add_run()
        hdr_row.cells[k].paragraphs[0].runs[0].text = htext
        hdr_row.cells[k].paragraphs[0].runs[0].bold = True
        hdr_row.cells[k].paragraphs[0].runs[0].font.size = Pt(font_pt)

    for row_vals in rows:
        new_row = t.add_row()
        while len(new_row.cells) < len(head):
            last_tc = new_row.cells[-1]._tc
            new_tc = copy.deepcopy(last_tc)
            last_tc.addnext(new_tc)
        while len(new_row.cells) > len(head):
            t._tbl.rows[-1]._tr.remove(new_row.cells[-1]._tc)
        for k, v in enumerate(row_vals):
            c = new_row.cells[k]
            for para in c.paragraphs:
                for rn in list(para.runs):
                    rn.text = ""
            if not c.paragraphs[0].runs:
                c.paragraphs[0].add_run()
            c.paragraphs[0].runs[0].text = str(v)
            c.paragraphs[0].runs[0].bold = False
            c.paragraphs[0].runs[0].font.size = Pt(font_pt)

    if col_widths:
        for row in t.rows:
            for k, w in enumerate(col_widths):
                if k < len(row.cells):
                    row.cells[k].width = Cm(w)

    trail_p = para_after_element(new_tbl_element, cap_p._parent)
    return t, trail_p
