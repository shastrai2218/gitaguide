"""Tests for the gold set and its staleness guard.

CI-safe: nothing here needs a built corpus (``corpus/data/gita.json`` is a
git-ignored artifact) or a database. The staleness checks run against a small
synthetic corpus; the structural checks read the committed gold file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus.schemas import Corpus, ScriptureSource, Verse
from eval.retrieval_eval import check_gold_staleness, fingerprint_of

GOLD_PATH = Path(__file__).resolve().parent.parent / "eval" / "gold_pairs.json"
VALID_INTENTS = {
    "seek_guidance",
    "emotional_support",
    "professional_dilemma",
    "scripture_question",
    "crisis",
    "other",
}


@pytest.fixture(scope="module")
def gold() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def _corpus() -> Corpus:
    return Corpus(
        verses=[
            Verse(
                id="BG-2.47",
                source=ScriptureSource.BHAGAVAD_GITA,
                chapter=2,
                verse=47,
                text_english="But you have only the right to work, but none to the fruit of it.",
            ),
            Verse(
                id="AS-1.9.1",
                source=ScriptureSource.ARTHASHASTRA,
                book=1,
                chapter=9,
                verse=1,
                text_english="NATIVE, born of high family, influential, well trained in arts.",
            ),
        ]
    )


# Fingerprints
def test_fingerprint_is_the_opening_words_lowercased() -> None:
    assert fingerprint_of("But you have only the right to work, but none to the fruit") == (
        "but you have only the right to work,"
    )


def test_fingerprint_is_whitespace_insensitive() -> None:
    assert fingerprint_of("a  b\n c") == fingerprint_of("a b c")


# Staleness detection - the three ways a gold id rots
def test_clean_gold_set_reports_nothing() -> None:
    gold = {
        "pairs": [
            {
                "id": "q1",
                "relevant": ["BG-2.47"],
                "fingerprints": {"BG-2.47": "But you have only the right to work,"},
            }
        ]
    }
    assert check_gold_staleness(gold, _corpus()) == []


def test_detects_a_moved_id() -> None:
    """The Sivananda -> Purohit case: same id, different text underneath."""
    gold = {
        "pairs": [
            {
                "id": "q1",
                "relevant": ["BG-2.47"],
                # Sivananda's wording for the same verse id.
                "fingerprints": {"BG-2.47": "Thy right is to work alone and never"},
            }
        ]
    }
    problems = check_gold_staleness(gold, _corpus())
    assert len(problems) == 1
    assert "MOVED" in problems[0]
    assert "BG-2.47" in problems[0]


def test_detects_a_deleted_id() -> None:
    """The AS-1.19.34 case: re-segmentation removed the sutra entirely."""
    gold = {
        "pairs": [
            {
                "id": "q1",
                "relevant": ["AS-1.19.34"],
                "fingerprints": {"AS-1.19.34": "The ruler should divide the day"},
            }
        ]
    }
    problems = check_gold_staleness(gold, _corpus())
    assert len(problems) == 1
    assert "NO LONGER EXISTS" in problems[0]


def test_detects_a_missing_fingerprint() -> None:
    gold = {"pairs": [{"id": "q1", "relevant": ["BG-2.47"], "fingerprints": {}}]}
    problems = check_gold_staleness(gold, _corpus())
    assert len(problems) == 1
    assert "no stored fingerprint" in problems[0]


# The committed gold set's structure
def test_gold_set_size_and_stratification(gold: dict) -> None:
    pairs = gold["pairs"]
    assert len(pairs) == 40, "40 queries keeps the metric quantum at 2.5 points"
    counts: dict[str, int] = {}
    for pair in pairs:
        counts[pair["category"]] = counts.get(pair["category"], 0) + 1
    assert counts == {
        "emotional": 12,
        "professional": 12,
        "statecraft": 8,
        "relationship": 4,
        "hinglish": 4,
    }


def test_every_query_has_two_to_five_gold_ids(gold: dict) -> None:
    """Multi-id grading is the point: near-synonymous verses are common."""
    for pair in gold["pairs"]:
        assert 2 <= len(pair["relevant"]) <= 5, pair["id"]


def test_every_gold_id_has_a_fingerprint_and_a_grade(gold: dict) -> None:
    for pair in gold["pairs"]:
        for verse_id in pair["relevant"]:
            assert verse_id in pair["fingerprints"], f"{pair['id']}/{verse_id}"
            assert pair["fingerprints"][verse_id].strip(), f"{pair['id']}/{verse_id}"
            assert pair["graded"][verse_id] in (1, 2), f"{pair['id']}/{verse_id}"


def test_ids_are_unique_and_intents_valid(gold: dict) -> None:
    ids = [pair["id"] for pair in gold["pairs"]]
    assert len(ids) == len(set(ids))
    for pair in gold["pairs"]:
        assert pair["expected_intent"] in VALID_INTENTS, pair["id"]


def test_no_crisis_queries(gold: dict) -> None:
    """Crisis turns short-circuit at the safety gate, so they cannot be scored."""
    for pair in gold["pairs"]:
        assert pair["expected_intent"] != "crisis", pair["id"]


def test_gold_set_declares_both_corpora(gold: dict) -> None:
    assert set(gold["corpus"]) == {
        "corpus/data/gita.json",
        "corpus/data/arthashastra.json",
    }


def test_hinglish_stratum_is_actually_code_mixed(gold: dict) -> None:
    devanagari_romanised = ("mujhe", "mein", "hain", "nahi", "kya", "bahut", "chahte")
    hinglish = [p for p in gold["pairs"] if p["category"] == "hinglish"]
    for pair in hinglish:
        assert any(token in pair["query"].lower() for token in devanagari_romanised), pair["id"]
