"""Authoring aid for the gold set - draws candidate verses from the live index.

Not part of the pipeline and not run by pytest. It exists so gold ids are chosen
by **content** rather than by remembered verse number: the previous gold set
picked Arthashastra ids positionally and three of five went stale when the
corpus was re-segmented.

For each authored query it prints the top vector hits and the top full-text hits
from the live Neo4j index, with the opening words of each verse, so a human (or
the author of the gold set) can pick acceptable answers by reading them.

Usage
-----
::

    python -m eval.gold_queries_draft > candidates.txt
    python -m eval.gold_queries_draft --category statecraft --top-k 12
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from app.config import get_settings
from app.embeddings import build_embedder

#: ``(id, category, expected_intent, query)``.
#:
#: Stratified per the eval design: 12 emotional, 12 professional, 8 statecraft,
#: 4 relationship, 4 code-mixed Hinglish. No crisis queries - those short-circuit
#: at the safety gate before retrieval ever runs, so they cannot be scored here.
QUERIES: tuple[tuple[str, str, str, str], ...] = (
    # ---- emotional / spiritual (12) ---------------------------------------
    ("gp-001", "emotional", "seek_guidance", "I feel like a failure after my exam results"),
    ("gp-002", "emotional", "emotional_support",
     "My father passed away last month and I cannot stop crying"),
    ("gp-003", "emotional", "emotional_support",
     "I am anxious all the time about my future and cannot sleep"),
    ("gp-004", "emotional", "emotional_support",
     "I hate myself for the mistakes I have made"),
    ("gp-005", "emotional", "seek_guidance",
     "Nothing I do seems to matter. What is the point of any of this?"),
    ("gp-006", "emotional", "emotional_support",
     "I am jealous of my friends who are doing better than me"),
    ("gp-007", "emotional", "seek_guidance",
     "How do I stop worrying about things I cannot control?"),
    ("gp-008", "emotional", "seek_guidance",
     "I feel restless and my mind never settles"),
    ("gp-009", "emotional", "emotional_support", "I am afraid of dying"),
    ("gp-010", "emotional", "seek_guidance",
     "I keep chasing success but I never feel satisfied"),
    ("gp-011", "emotional", "seek_guidance",
     "How do I find peace when everything around me is chaotic?"),
    ("gp-012", "emotional", "emotional_support",
     "I feel alone even when I am surrounded by people"),
    # ---- professional dilemma (12) ----------------------------------------
    ("gp-013", "professional", "professional_dilemma",
     "My manager takes credit for my work and plays politics"),
    ("gp-014", "professional", "professional_dilemma",
     "Should I quit my stable job to join a startup?"),
    ("gp-015", "professional", "professional_dilemma",
     "I am working sixteen hours a day and I am burnt out"),
    ("gp-016", "professional", "professional_dilemma",
     "A colleague keeps provoking me in meetings and I want to lash out"),
    ("gp-017", "professional", "professional_dilemma",
     "There is corruption in my department but reporting it could cost my job"),
    ("gp-018", "professional", "professional_dilemma",
     "I did not get the promotion I deserved"),
    ("gp-019", "professional", "professional_dilemma",
     "How do I decide whom to trust with responsibility in my team?"),
    ("gp-020", "professional", "professional_dilemma",
     "My boss micromanages every small thing I do"),
    ("gp-021", "professional", "professional_dilemma",
     "Should I take a job that pays more but bores me?"),
    ("gp-022", "professional", "professional_dilemma",
     "I made a costly mistake at work and I am terrified of the consequences"),
    ("gp-023", "professional", "professional_dilemma",
     "My team keeps missing deadlines and I do not know how to fix it"),
    ("gp-024", "professional", "professional_dilemma",
     "I have to dismiss someone from my team for the first time"),
    # ---- statecraft / leadership (8) --------------------------------------
    ("gp-025", "statecraft", "professional_dilemma",
     "How should a leader choose ministers and advisers?"),
    ("gp-026", "statecraft", "professional_dilemma",
     "How do I audit the people who handle money in my organisation?"),
    ("gp-027", "statecraft", "professional_dilemma",
     "When should I make peace with a rival instead of fighting?"),
    ("gp-028", "statecraft", "professional_dilemma",
     "How should a ruler divide and organise his working day?"),
    ("gp-029", "statecraft", "professional_dilemma",
     "How do I detect and handle a conspiracy inside my own camp?"),
    ("gp-030", "statecraft", "professional_dilemma",
     "What makes a treaty or an alliance worth entering?"),
    ("gp-031", "statecraft", "professional_dilemma",
     "How should punishment be applied so it is neither too harsh nor too lenient?"),
    ("gp-032", "statecraft", "professional_dilemma",
     "How do I gather reliable information about what is really happening?"),
    # ---- relationship / family (4) ----------------------------------------
    ("gp-033", "relationship", "seek_guidance",
     "My parents want me to marry someone I do not love"),
    ("gp-034", "relationship", "seek_guidance",
     "I had a bitter fight with my brother over family property"),
    ("gp-035", "relationship", "seek_guidance",
     "My wife and I keep arguing about money"),
    ("gp-036", "relationship", "seek_guidance",
     "How do I care for my ageing parents without resenting them?"),
    # ---- code-mixed Hinglish (4) ------------------------------------------
    ("gp-037", "hinglish", "emotional_support",
     "mujhe lagta hai sab kuch meri galti hai, I can't focus on anything"),
    ("gp-038", "hinglish", "professional_dilemma",
     "office mein sab log politics karte hain, main kya karun"),
    ("gp-039", "hinglish", "seek_guidance",
     "papa chahte hain main unka business sambhalun but mera mann nahi hai"),
    ("gp-040", "hinglish", "emotional_support",
     "bahut tension hai exam ka, neend nahi aati raat ko"),
)

VECTOR_QUERY = """
CALL db.index.vector.queryNodes($index, $k, $vector) YIELD node, score
RETURN node.id AS id, node.text_english AS text, score
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes($index, $q, {limit: $k}) YIELD node, score
RETURN node.id AS id, node.text_english AS text, score
"""


def first_words(text: str, words: int = 14) -> str:
    return " ".join(str(text).split()[:words])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--category", default=None)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)

    from neo4j import GraphDatabase

    settings = get_settings().model_copy(update={"mock_mode": False, "use_real_neo4j": True})
    embedder = build_embedder(settings)
    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )

    def run(cypher: str, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = driver.execute_query(
            cypher, params, database_=settings.neo4j_database
        )
        return [r.data() for r in records]

    try:
        for qid, category, intent, query in QUERIES:
            if args.category and category != args.category:
                continue
            print(f"\n{'=' * 100}")
            print(f"{qid}  [{category} / expect {intent}]  {query}")
            print("=" * 100)

            hits = run(
                VECTOR_QUERY,
                index=settings.neo4j_vector_index,
                k=args.top_k,
                vector=embedder.embed_one(query),
            )
            print("  -- vector --")
            for h in hits:
                print(f"    {h['id']:14s} {h['score']:.3f}  {first_words(h['text'])}")

            # Full-text is quoted-free: Lucene syntax would choke on apostrophes.
            safe = " ".join(w for w in query.replace("?", " ").split() if w.isalnum())
            ft = run(
                FULLTEXT_QUERY, index=settings.neo4j_fulltext_index, q=safe, k=5
            )
            print("  -- fulltext --")
            for h in ft:
                print(f"    {h['id']:14s} {h['score']:.3f}  {first_words(h['text'])}")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
