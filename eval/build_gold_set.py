"""Assemble ``eval/gold_pairs.json`` and a reviewable markdown table.

Gold entries are authored here as ``(query, category, expected intent, {verse
id: grade})`` and this script verifies every id against the built corpus, then
writes:

* ``eval/gold_pairs.json`` - the eval instrument, with a text fingerprint stored
  next to each gold id so a future corpus rebuild that moves the id is caught by
  ``eval.retrieval_eval.check_gold_staleness`` instead of silently changing what
  the metric means;
* ``docs/gold_set_review.md`` - the same content as a table, so the entries can
  be reviewed and vetoed before the set is frozen.

Fails loud: an id that is not in the corpus aborts the build rather than being
dropped, because a gold set that quietly loses entries is a gold set whose
numbers are not comparable across runs.

Grades: 2 = a domain reader would call this a primary answer, 1 = acceptable.
Both count as a hit; the grades only feed nDCG.

Usage
-----
::

    python -m eval.build_gold_set
    python -m eval.build_gold_set --check      # verify only, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.config import CORPUS_DATA_DIR, PROJECT_ROOT
from corpus.schemas import Corpus

GOLD_PATH = Path(__file__).with_name("gold_pairs.json")
REVIEW_PATH = PROJECT_ROOT / "docs" / "gold_set_review.md"
CORPUS_FILES = ("corpus/data/gita.json", "corpus/data/arthashastra.json")

#: ``(id, category, expected_intent, query, {verse_id: grade}, note)``
#:
#: Ids were chosen by reading the corpus (``eval.gold_queries_draft`` dumps
#: candidates from the live index, plus content searches), never by remembering
#: a chapter/verse number - the previous set picked Arthashastra ids positionally
#: and three of five went stale.
ENTRIES: tuple[tuple[str, str, str, str, dict[str, int], str], ...] = (
    # ---- emotional / spiritual -------------------------------------------
    ("gp-001", "emotional", "seek_guidance",
     "I feel like a failure after my exam results",
     {"BG-2.47": 2, "BG-2.38": 2, "BG-18.47": 1, "BG-6.5": 1},
     "Outcome vs effort; equanimity in victory and defeat."),
    ("gp-002", "emotional", "emotional_support",
     "My father passed away last month and I cannot stop crying",
     {"BG-2.11": 2, "BG-2.20": 2, "BG-2.13": 1, "BG-18.66": 1},
     "The canonical bereavement passage of chapter 2."),
    ("gp-003", "emotional", "emotional_support",
     "I am anxious all the time about my future and cannot sleep",
     {"BG-2.47": 2, "BG-6.35": 2, "BG-6.34": 1, "BG-2.48": 1},
     "Anticipatory anxiety: outcome-detachment plus the restless-mind exchange."),
    ("gp-004", "emotional", "emotional_support",
     "I hate myself for the mistakes I have made",
     {"BG-6.5": 2, "BG-6.6": 2, "BG-18.66": 1},
     "Self as friend or enemy; surrender and absolution."),
    ("gp-005", "emotional", "seek_guidance",
     "Nothing I do seems to matter. What is the point of any of this?",
     {"BG-3.35": 2, "BG-2.47": 2, "BG-3.9": 1, "BG-16.8": 1},
     "Meaninglessness; BG-16.8 states the nihilist position the text answers."),
    ("gp-006", "emotional", "emotional_support",
     "I am jealous of my friends who are doing better than me",
     {"BG-4.22": 2, "BG-3.35": 2, "BG-2.38": 1},
     "Contentment with what comes unbidden; one's own path over another's."),
    ("gp-007", "emotional", "seek_guidance",
     "How do I stop worrying about things I cannot control?",
     {"BG-2.47": 2, "BG-3.19": 2, "BG-2.48": 1},
     "The core nishkama-karma cluster."),
    ("gp-008", "emotional", "seek_guidance",
     "I feel restless and my mind never settles",
     {"BG-6.34": 2, "BG-6.35": 2, "BG-6.26": 1, "BG-6.24": 1},
     "Arjuna's own complaint about the fickle mind, and the reply."),
    ("gp-009", "emotional", "emotional_support",
     "I am afraid of dying",
     {"BG-2.20": 2, "BG-2.13": 2, "BG-2.11": 1},
     "Continuity of the self through death."),
    ("gp-010", "emotional", "seek_guidance",
     "I keep chasing success but I never feel satisfied",
     {"BG-2.47": 2, "BG-4.22": 2, "BG-2.48": 1, "BG-16.21": 1},
     "Craving as the engine of dissatisfaction."),
    ("gp-011", "emotional", "seek_guidance",
     "How do I find peace when everything around me is chaotic?",
     {"BG-2.64": 2, "BG-2.65": 2, "BG-6.15": 1, "BG-18.62": 1},
     "Peace as an inner state reached through self-control."),
    ("gp-012", "emotional", "emotional_support",
     "I feel alone even when I am surrounded by people",
     {"BG-6.30": 2, "BG-9.29": 2, "BG-9.4": 1},
     "The devotional answer to isolation: never forsaken, present in all."),
    # ---- professional dilemma --------------------------------------------
    ("gp-013", "professional", "professional_dilemma",
     "My manager takes credit for my work and plays politics",
     {"BG-2.47": 2, "AS-2.8.20": 1, "AS-2.9.1": 1, "AS-2.8.13": 1},
     "Gita for the grievance, Arthashastra for misappropriation and audit."),
    ("gp-014", "professional", "professional_dilemma",
     "Should I quit my stable job to join a startup?",
     {"BG-3.35": 2, "BG-18.47": 2, "BG-2.47": 1},
     "Svadharma: one's own imperfect work over another's done well."),
    ("gp-015", "professional", "professional_dilemma",
     "I am working sixteen hours a day and I am burnt out",
     {"AS-1.19.2": 2, "AS-1.19.3": 2, "AS-2.7.8": 1},
     "Kautilya's division of the day and night into eight parts each."),
    ("gp-016", "professional", "professional_dilemma",
     "A colleague keeps provoking me in meetings and I want to lash out",
     {"BG-2.62": 2, "BG-16.21": 2, "BG-5.23": 1, "AS-1.6.1": 1},
     "The attachment-desire-anger chain, and restraint of the senses."),
    ("gp-017", "professional", "professional_dilemma",
     "There is corruption in my department but reporting it could cost my job",
     {"AS-2.8.13": 2, "AS-2.8.20": 2, "AS-2.5.17": 1, "AS-2.9.1": 1},
     "The forty ways of embezzlement and what follows from proof."),
    ("gp-018", "professional", "professional_dilemma",
     "I did not get the promotion I deserved",
     {"BG-2.47": 2, "BG-2.38": 2, "BG-4.22": 1, "BG-3.35": 1},
     "Entitlement to effort, not to outcome."),
    ("gp-019", "professional", "professional_dilemma",
     "How do I decide whom to trust with responsibility in my team?",
     {"AS-1.9.1": 2, "AS-2.9.1": 2, "AS-1.8.6": 1, "AS-1.16.1": 1},
     "The amatya-sampat qualification list."),
    ("gp-020", "professional", "professional_dilemma",
     "My boss micromanages every small thing I do",
     {"BG-3.35": 2, "AS-2.9.1": 1, "AS-1.16.1": 1},
     "Delegation by capacity; doing one's own assigned work."),
    ("gp-021", "professional", "professional_dilemma",
     "Should I take a job that pays more but bores me?",
     {"BG-3.35": 2, "BG-18.47": 2, "BG-2.47": 1},
     "Same svadharma cluster as gp-014, different framing."),
    ("gp-022", "professional", "professional_dilemma",
     "I made a costly mistake at work and I am terrified of the consequences",
     {"BG-2.38": 2, "BG-18.66": 2, "BG-6.5": 1, "BG-2.47": 1},
     "Fear of consequence; absolution and self-rescue."),
    ("gp-023", "professional", "professional_dilemma",
     "My team keeps missing deadlines and I do not know how to fix it",
     {"AS-3.14.1": 2, "AS-3.14.3": 2, "AS-1.19.2": 1, "AS-2.9.1": 1},
     "Kautilya on work not done for wages received."),
    ("gp-024", "professional", "professional_dilemma",
     "I have to dismiss someone from my team for the first time",
     {"AS-2.8.20": 2, "AS-3.14.1": 1, "AS-2.9.1": 1, "AS-1.9.1": 1},
     "Proof before penalty; fitness for the post."),
    # ---- statecraft / leadership -----------------------------------------
    ("gp-025", "statecraft", "professional_dilemma",
     "How should a leader choose ministers and advisers?",
     {"AS-1.9.1": 2, "AS-1.8.6": 2, "AS-1.8.8": 1, "AS-1.16.1": 1},
     "The debate between the schools on how to appoint ministers."),
    ("gp-026", "statecraft", "professional_dilemma",
     "How do I audit the people who handle money in my organisation?",
     {"AS-2.8.13": 2, "AS-2.8.20": 2, "AS-2.5.17": 1, "AS-2.9.10": 1},
     "Embezzlement detection and departmental accounting."),
    ("gp-027", "statecraft", "professional_dilemma",
     "When should I make peace with a rival instead of fighting?",
     {"AS-7.1.5": 2, "AS-7.3.16": 2, "AS-7.17.2": 1, "AS-6.2.3": 1},
     "Shadgunya: the inferior makes peace, the superior makes war."),
    ("gp-028", "statecraft", "professional_dilemma",
     "How should a ruler divide and organise his working day?",
     {"AS-1.19.2": 2, "AS-1.19.3": 2, "AS-1.19.5": 2},
     "The eight nalikas of day and of night."),
    ("gp-029", "statecraft", "professional_dilemma",
     "How do I detect and handle a conspiracy inside my own camp?",
     {"AS-1.16.10": 2, "AS-11.1.4": 2, "AS-1.14.2": 1},
     "Spies among corporations; seditious parties."),
    ("gp-030", "statecraft", "professional_dilemma",
     "What makes a treaty or an alliance worth entering?",
     {"AS-7.17.2": 2, "AS-7.1.5": 2, "AS-7.3.16": 1},
     "Peace resting on honesty or oath versus on a surety."),
    ("gp-031", "statecraft", "professional_dilemma",
     "How should punishment be applied so it is neither too harsh nor too lenient?",
     {"AS-7.5.16": 2, "AS-4.11.16": 1, "AS-3.18.1": 1},
     "Mis-application of danda as a cause of disaffection. Thin coverage."),
    ("gp-032", "statecraft", "professional_dilemma",
     "How do I gather reliable information about what is really happening?",
     {"AS-1.16.10": 2, "AS-1.10.3": 1, "AS-11.1.4": 1, "AS-1.10.5": 1},
     "The spy apparatus as an intelligence system."),
    # ---- relationship / family -------------------------------------------
    ("gp-033", "relationship", "seek_guidance",
     "My parents want me to marry someone I do not love",
     {"BG-3.35": 2, "BG-18.47": 1, "AS-1.3.8": 1},
     "Weak corpus coverage: no direct teaching, only svadharma by analogy."),
    ("gp-034", "relationship", "seek_guidance",
     "I had a bitter fight with my brother over family property",
     {"AS-3.5.4": 2, "AS-3.1.5": 1, "BG-2.62": 1, "BG-16.21": 1},
     "Inheritance rules plus the anger chain."),
    ("gp-035", "relationship", "seek_guidance",
     "My wife and I keep arguing about money",
     {"AS-3.2.4": 2, "BG-2.62": 1, "BG-16.21": 1},
     "Stridhana; the corpus is legalistic here, not counselling."),
    ("gp-036", "relationship", "seek_guidance",
     "How do I care for my ageing parents without resenting them?",
     {"BG-2.13": 2, "BG-3.35": 1, "AS-1.5.6": 1},
     "Weakest entry: the corpus has no filial-care teaching. Kept as an "
     "honest negative - if retrieval scores well here it is luck."),
    # ---- code-mixed Hinglish ---------------------------------------------
    ("gp-037", "hinglish", "emotional_support",
     "mujhe lagta hai sab kuch meri galti hai, I can't focus on anything",
     {"BG-6.5": 2, "BG-6.6": 2, "BG-18.66": 1},
     "Hinglish twin of gp-004 (self-blame)."),
    ("gp-038", "hinglish", "professional_dilemma",
     "office mein sab log politics karte hain, main kya karun",
     {"BG-2.47": 2, "AS-2.8.20": 1, "AS-2.9.1": 1},
     "Hinglish twin of gp-013 (office politics)."),
    ("gp-039", "hinglish", "seek_guidance",
     "papa chahte hain main unka business sambhalun but mera mann nahi hai",
     {"BG-3.35": 2, "BG-18.47": 2, "BG-2.47": 1},
     "Hinglish twin of gp-014/gp-033 (svadharma vs family expectation)."),
    ("gp-040", "hinglish", "emotional_support",
     "bahut tension hai exam ka, neend nahi aati raat ko",
     {"BG-2.47": 2, "BG-6.35": 2, "BG-2.38": 1},
     "Hinglish twin of gp-003 (exam anxiety, sleeplessness)."),
)


def load_corpus() -> Corpus:
    corpus = Corpus()
    for name in CORPUS_FILES:
        path = PROJECT_ROOT / name
        if not path.exists():
            raise FileNotFoundError(
                f"{path} is missing - build the corpora first "
                "(python -m corpus.load_gita; python -m corpus.build_arthashastra)"
            )
        corpus.extend(Corpus.load(path))
    return corpus


def fingerprint(text: str, words: int = 8) -> str:
    return " ".join(str(text).split()[:words])


def build(corpus: Corpus) -> dict[str, object]:
    by_id = {verse.id: verse for verse in corpus}
    missing: list[str] = []
    pairs: list[dict[str, object]] = []

    for qid, category, intent, query, graded, note in ENTRIES:
        for verse_id in graded:
            if verse_id not in by_id:
                missing.append(f"{qid}: {verse_id}")
        if not 2 <= len(graded) <= 5:
            raise ValueError(f"{qid}: expected 2-5 gold ids, got {len(graded)}")
        pairs.append(
            {
                "id": qid,
                "category": category,
                "query": query,
                "expected_intent": intent,
                "relevant": list(graded),
                "graded": dict(graded),
                "fingerprints": {
                    vid: fingerprint(by_id[vid].text_english)
                    for vid in graded
                    if vid in by_id
                },
                "note": note,
            }
        )

    if missing:
        raise ValueError(
            "gold ids not present in the corpus (refusing to write a gold set "
            "with dangling entries):\n  " + "\n  ".join(missing)
        )

    return {
        "description": (
            "GitaGuide retrieval gold set. 40 queries stratified by intent; 2-5 "
            "acceptable verse ids each, graded 2 (primary) or 1 (acceptable). A "
            "query counts as a hit if ANY acceptable id is retrieved - near-"
            "synonymous verses are common (BG-2.47 and BG-3.19 teach the same "
            "thing), so single-id grading measured wording luck rather than "
            "retrieval. Each id carries a text fingerprint; the eval harness "
            "warns when a rebuilt corpus moves an id out from under it. No crisis "
            "queries: those short-circuit at the safety gate before retrieval."
        ),
        "corpus": list(CORPUS_FILES),
        "pairs": pairs,
    }


def write_review(gold: dict[str, object], corpus: Corpus, path: Path) -> None:
    by_id = {verse.id: verse for verse in corpus}
    lines = [
        "# Gold set review",
        "",
        "Generated by `python -m eval.build_gold_set`. Review and veto entries "
        "here before treating the numbers as a baseline.",
        "",
        f"- **{len(gold['pairs'])} queries**, "
        f"{sum(len(p['relevant']) for p in gold['pairs'])} gold ids "  # type: ignore[index]
        f"({sum(len(p['relevant']) for p in gold['pairs']) / len(gold['pairs']):.1f} per query)",  # type: ignore[index]
        "- Grading: **2** = primary answer, **1** = acceptable. A query is a hit "
        "if *any* acceptable id is retrieved.",
        "- Verse ids are addresses, not identities: the `fingerprint` column is "
        "what makes staleness detectable after a corpus rebuild.",
        "",
    ]
    by_category: dict[str, list[dict[str, object]]] = {}
    for pair in gold["pairs"]:  # type: ignore[union-attr]
        by_category.setdefault(str(pair["category"]), []).append(pair)

    for category, items in by_category.items():
        lines += [f"## {category} ({len(items)} queries)", ""]
        for pair in items:
            lines += [
                f"### {pair['id']} — {pair['query']}",
                "",
                f"*expected intent:* `{pair['expected_intent']}` — {pair['note']}",
                "",
                "| grade | verse | opening words |",
                "| --- | --- | --- |",
            ]
            graded: dict[str, int] = pair["graded"]  # type: ignore[assignment]
            for verse_id, grade in sorted(graded.items(), key=lambda kv: (-kv[1], kv[0])):
                opening = " ".join(by_id[verse_id].text_english.split()[:18])
                lines.append(f"| {grade} | `{verse_id}` | {opening}… |")
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="verify only, write nothing")
    parser.add_argument("--out", default=str(GOLD_PATH))
    parser.add_argument("--review", default=str(REVIEW_PATH))
    args = parser.parse_args(argv)

    corpus = load_corpus()
    gold = build(corpus)

    counts: dict[str, int] = {}
    for pair in gold["pairs"]:  # type: ignore[union-attr]
        counts[str(pair["category"])] = counts.get(str(pair["category"]), 0) + 1
    print(f"corpus: {len(corpus)} verses ({CORPUS_DATA_DIR})")
    print(f"gold  : {len(gold['pairs'])} queries")  # type: ignore[arg-type]
    for category, n in sorted(counts.items()):
        print(f"        {category:14s} {n}")
    ids = {vid for pair in gold["pairs"] for vid in pair["relevant"]}  # type: ignore[union-attr]
    print(f"        {len(ids)} distinct gold ids, all present in the corpus")

    if args.check:
        print("\n--check: nothing written")
        return 0

    Path(args.out).write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review(gold, corpus, Path(args.review))
    print(f"\nwrote {args.out}")
    print(f"wrote {args.review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
