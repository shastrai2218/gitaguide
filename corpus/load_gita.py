"""Build the Bhagavad Gita half of the corpus.

Research-pipeline role
---------------------
Downloads (or reads from disk) the open ``gita/gita`` dataset, joins verses with
an English translation, applies weak labels from :mod:`corpus.tagging`, and
writes ``corpus/data/gita.json`` in the :class:`corpus.schemas.Verse` schema so
:mod:`kg.build_graph` can load it without knowing anything about the source
format.

Usage
-----
::

    python -m corpus.load_gita                      # download + build
    python -m corpus.load_gita --offline            # copy the bundled sample
    python -m corpus.load_gita --verse-file v.json --translation-file t.json
    python -m corpus.load_gita --limit 50 --out corpus/data/gita_small.json

Offline mode is a first-class path (rule 1): with ``--offline`` the script needs
no network and still produces a valid, loadable corpus file.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config import CORPUS_DATA_DIR, SAMPLE_CORPUS_PATH
from corpus.schemas import Corpus, ScriptureSource, Verse, make_gita_id
from corpus.tagging import coverage_report, tag_all

logger = logging.getLogger(__name__)

#: Raw JSON from the ``gita/gita`` repository (CC-licensed community dataset).
DEFAULT_VERSE_URL = "https://raw.githubusercontent.com/gita/gita/main/data/verse.json"
DEFAULT_TRANSLATION_URL = (
    "https://raw.githubusercontent.com/gita/gita/main/data/translation.json"
)

#: Translations to prefer, best first.
#:
#: Shri Purohit Swami (d. 1941) leads on **licensing**, not style: his
#: translation is public domain in India (life+60, so since 2001) and in the
#: UK/EU (life+70, since 2011), which makes the corpus commercially usable in
#: every target jurisdiction. Swami Sivananda (d. 1963) is public domain in
#: India only since 2024 and not in the UK/EU until 2033, so he is a fallback.
#: The upstream repo's Unlicense covers its compilation, not the underlying
#: translations - a dataset cannot dedicate a third party's work to the public
#: domain, so the translator's own death date is what governs.
PREFERRED_AUTHORS: tuple[str, ...] = (
    "Shri Purohit Swami",
    "Swami Sivananda",
    "Swami Gambirananda",
    "Dr. S. Sankaranarayan",
)

HTTP_TIMEOUT = 30


def fetch_json(url: str, timeout: int = HTTP_TIMEOUT) -> Any:
    """GET a JSON document, raising a readable error on failure."""
    logger.info("downloading %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": "GitaGuide/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not fetch {url}: {exc}") from exc


def load_local_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


#: Editorial marker left in the upstream dataset, not part of the translation.
#: BG-2.48's Purohit entry literally begins ``"Revised:\n"`` before the verse.
#:
#: Deliberately a literal, case-sensitive, anchored match rather than a general
#: ``^\w+:`` rule: verses legitimately open with a speaker attribution
#: ("Lord Shri Krishna said:", "Arjuna asked:"), and a loose prefix rule would
#: silently eat those. As of the 2026-09 dataset this fires on exactly 1 verse
#: of 701, which is why it logs - if the count ever grows, the upstream data has
#: changed and we want to hear about it rather than quietly cleaning more text.
SOURCE_ARTIFACT_PREFIXES: tuple[str, ...] = ("Revised:",)


def strip_source_artifacts(text: str, verse_ref: str = "") -> str:
    """Remove known upstream editorial prefixes from a translation.

    Logs a warning on every hit so the scope of the cleaning stays visible.
    """
    cleaned = text
    for prefix in SOURCE_ARTIFACT_PREFIXES:
        if cleaned.lstrip().startswith(prefix):
            stripped = cleaned.lstrip()[len(prefix) :].lstrip()
            logger.warning(
                "stripped source artifact %r from BG-%s (upstream dataset "
                "editorial marker, not verse text)",
                prefix,
                verse_ref or "?",
            )
            cleaned = stripped
    return cleaned


def _as_records(payload: Any) -> list[dict[str, Any]]:
    """Accept a bare list or ``{"rows"|"data"|"verses": [...]}``."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, Mapping):
        for key in ("rows", "data", "verses", "translations"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("unrecognised JSON shape for the gita dataset")


def index_translations(records: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """``verse_id -> best English translation`` using :data:`PREFERRED_AUTHORS`."""
    best: dict[int, tuple[int, str]] = {}
    for record in records:
        language = str(record.get("lang") or record.get("language") or "english").lower()
        if language not in {"english", "en"}:
            continue
        text = str(
            record.get("description") or record.get("translation") or record.get("text") or ""
        ).strip()
        if not text:
            continue
        raw_id = record.get("verse_id") or record.get("verseId") or record.get("id")
        try:
            verse_id = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue

        author = str(record.get("author_name") or record.get("authorName") or "")
        rank = (
            PREFERRED_AUTHORS.index(author)
            if author in PREFERRED_AUTHORS
            else len(PREFERRED_AUTHORS)
        )
        current = best.get(verse_id)
        if current is None or rank < current[0]:
            best[verse_id] = (rank, text)
    return {verse_id: text for verse_id, (_, text) in best.items()}


def build_verses(
    verse_records: Sequence[Mapping[str, Any]],
    translations: Mapping[int, str],
    limit: int | None = None,
) -> list[Verse]:
    """Join verses to translations, tag them, and validate against the schema."""
    verses: list[Verse] = []
    skipped = 0

    for record in verse_records:
        chapter = record.get("chapter_number") or record.get("chapter")
        number = record.get("verse_number") or record.get("verse")
        if chapter is None or number is None:
            skipped += 1
            continue

        raw_id = record.get("id") or record.get("verse_id")
        english = ""
        if raw_id is not None:
            try:
                english = translations.get(int(raw_id), "")
            except (TypeError, ValueError):
                english = ""
        english = english or str(record.get("translation") or "").strip()
        english = strip_source_artifacts(english, f"{chapter}.{number}")
        if not english:
            skipped += 1
            continue

        sanskrit = str(record.get("text") or record.get("slok") or "").strip()
        transliteration = str(
            record.get("transliteration") or record.get("verse_transliteration") or ""
        ).strip()
        labels = tag_all(english)

        try:
            verses.append(
                Verse(
                    id=make_gita_id(int(chapter), int(number)),
                    source=ScriptureSource.BHAGAVAD_GITA,
                    chapter=int(chapter),
                    verse=int(number),
                    text_sanskrit=sanskrit,
                    transliteration=transliteration,
                    text_english=english,
                    themes=labels["themes"],
                    emotions_addressed=labels["emotions_addressed"],
                    concepts=labels["concepts"],
                )
            )
        except ValueError as exc:
            logger.debug("skipping malformed verse %s.%s: %s", chapter, number, exc)
            skipped += 1

        if limit is not None and len(verses) >= limit:
            break

    verses.sort(key=lambda item: (item.chapter, item.verse))
    logger.info("built %d Gita verses (%d skipped)", len(verses), skipped)
    return verses


def load_gita(
    verse_source: str | None = None,
    translation_source: str | None = None,
    offline: bool = False,
    limit: int | None = None,
) -> Corpus:
    """Return a :class:`Corpus` of Gita verses.

    ``offline=True`` (or any download failure) falls back to the bundled sample
    corpus so downstream stages always have something to work with.
    """
    if offline:
        logger.info("offline mode: using bundled sample corpus")
        return _sample_gita()

    try:
        verse_payload = (
            load_local_json(verse_source)
            if verse_source and Path(verse_source).exists()
            else fetch_json(verse_source or DEFAULT_VERSE_URL)
        )
        translation_payload = (
            load_local_json(translation_source)
            if translation_source and Path(translation_source).exists()
            else fetch_json(translation_source or DEFAULT_TRANSLATION_URL)
        )
    except RuntimeError:
        logger.exception("download failed; falling back to the bundled sample corpus")
        return _sample_gita()

    verses = build_verses(
        _as_records(verse_payload), index_translations(_as_records(translation_payload)), limit
    )
    if not verses:
        logger.warning("no verses parsed; falling back to the bundled sample corpus")
        return _sample_gita()
    return Corpus(verses=verses)


def _sample_gita() -> Corpus:
    corpus = Corpus.load(SAMPLE_CORPUS_PATH)
    return Corpus(verses=corpus.by_source(ScriptureSource.BHAGAVAD_GITA))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Bhagavad Gita corpus file")
    parser.add_argument("--verse-file", dest="verse_file", default=None)
    parser.add_argument("--translation-file", dest="translation_file", default=None)
    parser.add_argument("--verse-url", dest="verse_url", default=DEFAULT_VERSE_URL)
    parser.add_argument("--translation-url", dest="translation_url", default=DEFAULT_TRANSLATION_URL)
    parser.add_argument("--offline", action="store_true", help="use the bundled sample corpus")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(CORPUS_DATA_DIR / "gita.json"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    corpus = load_gita(
        verse_source=args.verse_file or args.verse_url,
        translation_source=args.translation_file or args.translation_url,
        offline=args.offline,
        limit=args.limit,
    )
    path = corpus.save(args.out)
    coverage = coverage_report(verse.text_english for verse in corpus)
    logger.info("wrote %d verses to %s", len(corpus), path)
    logger.info("weak-label coverage: %s", coverage)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
