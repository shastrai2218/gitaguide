"""Download the Shamasastry (1915) Arthashastra from English Wikisource.

Fetches ``Arthashastra/Book_I`` .. ``Arthashastra/Book_XV`` through the MediaWiki
parse API, strips the Wikisource chrome, and writes plain text to
``data/arthashastra_raw/book_1.txt`` .. ``book_15.txt`` for
:mod:`corpus.build_arthashastra` to segment into ``AS-<book>.<chapter>.<sutra>``
units.

What is kept and what is stripped
---------------------------------
**Kept:** the translated body text and the ``CHAPTER I. ...`` heading lines - the
corpus builder keys section ids off those headings, so removing them would
collapse every book into one chapter.

**Stripped:** the header/nav/licence blocks (``.ws-header``, ``.ws-noexport``,
``.noprint``), ``.mw-editsection``, ``sup.reference`` footnote markers, style and
script tags, layout tables, "Retrieved from" lines, the translator/publisher
attribution, and the ``[Thus ends Chapter ...]`` colophons that close every
chapter (editorial apparatus, repeated ~150 times, and pure noise in a retrieval
index).

Note on structure: these pages carry no ``<h1>``-``<h4>`` elements. Chapter
titles are centred ``div``s, so text extraction walks leaf block elements rather
than looking for heading tags.

Fail-loud policy
----------------
A book under ``--min-words`` (default 500) is a scrape failure, not a short book.
The script raises before writing anything, so a partially-broken corpus can never
be silently committed. Per-book word counts are printed for eyeballing:
Book II is the largest, Books X-XV are short.

Usage
-----
::

    python -m scripts.download_arthashastra
    python -m scripts.download_arthashastra --books 2 3 --force
    python -m scripts.download_arthashastra --out-dir data/arthashastra_raw
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

API_URL = "https://en.wikisource.org/w/api.php"
PAGE_TEMPLATE = "Arthashastra/Book_{roman}"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "arthashastra_raw"

#: Wikisource asks for a descriptive agent with a contact/purpose.
USER_AGENT = (
    "GitaGuide/0.1 (final-year research project; corpus build; "
    "https://en.wikisource.org/wiki/Arthashastra)"
)

#: Books I-XV in the order they are published on Wikisource.
ROMAN_NUMERALS: tuple[str, ...] = (
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
    "IX", "X", "XI", "XII", "XIII", "XIV", "XV",
)

#: CSS selectors for everything that is chrome rather than translation.
BOILERPLATE_SELECTORS: tuple[str, ...] = (
    ".mw-editsection",
    ".noprint",
    ".ws-noexport",
    ".ws-header",
    ".ws-footer",
    ".wst-header-mainblock",
    ".licensetpl",
    ".catlinks",
    ".printfooter",
    "sup.reference",
    "style",
    "script",
    "table",
    "link",
)

#: Lines dropped after text extraction.
DROP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Editorial colophon closing each chapter.
    re.compile(r"^\[?\s*Thus ends\b", re.IGNORECASE),
    # MediaWiki page furniture.
    re.compile(r"^Retrieved from\b", re.IGNORECASE),
    re.compile(r"^Categor(?:y|ies)\s*:", re.IGNORECASE),
    re.compile(r"^This page was last edited\b", re.IGNORECASE),
    # Translator / publisher attribution.
    re.compile(
        r"^(?:Kautilya\.?\s*)?Artha[sś]h?[aá]stra\s*\.?\s*Translated by\b", re.IGNORECASE
    ),
    re.compile(r"^Translated by R\.?\s*Shamasastry\b", re.IGNORECASE),
    re.compile(r"^Bangalore\s*:\s*Government Press", re.IGNORECASE),
    re.compile(r"^←\s*", re.IGNORECASE),  # "← Table of Contents" nav arrow
    re.compile(r"^(?:Table of Contents|Contents)\s*:?$", re.IGNORECASE),
)

#: Sanity floor - anything shorter is a broken scrape, not a short book.
MIN_WORDS = 500
REQUEST_TIMEOUT = 60
SLEEP_SECONDS = 1.0


class DownloadError(RuntimeError):
    """A book could not be fetched or came back implausibly short."""


def roman_for(book: int) -> str:
    if not 1 <= book <= len(ROMAN_NUMERALS):
        raise ValueError(f"book must be 1..{len(ROMAN_NUMERALS)}, got {book}")
    return ROMAN_NUMERALS[book - 1]


def fetch_rendered_html(page: str, session: requests.Session) -> str:
    """Return the rendered HTML for a Wikisource page via ``action=parse``."""
    params = {
        "action": "parse",
        "page": page,
        "prop": "text",
        "formatversion": "2",
        "disableeditsection": "true",
        "format": "json",
    }
    response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise DownloadError(f"{page}: API error {payload['error'].get('info', payload['error'])}")
    try:
        return payload["parse"]["text"]
    except (KeyError, TypeError) as exc:
        raise DownloadError(f"{page}: unexpected API response shape") from exc


def html_to_text(html: str) -> str:
    """Strip Wikisource chrome and flatten the body to one block per line.

    Chapter headings survive as their own line, which is what
    :mod:`corpus.build_arthashastra` matches on.
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup

    for selector in BOILERPLATE_SELECTORS:
        for node in root.select(selector):
            node.decompose()

    lines: list[str] = []
    for node in root.find_all(["p", "div", "dd", "li", "blockquote"]):
        # Only leaf blocks, so nested containers are not emitted twice.
        if node.find(["p", "div", "blockquote"], recursive=False):
            continue
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        text = re.sub(r"\[\d+\]", "", text)  # leftover footnote markers
        text = re.sub(r"\s+", " ", text).strip()
        if not text or any(pattern.match(text) for pattern in DROP_LINE_PATTERNS):
            continue
        lines.append(text)

    return "\n\n".join(lines) + "\n"


def word_count(text: str) -> int:
    return len(text.split())


def chapter_headings(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*CHAPTER\b", line, re.IGNORECASE)
    ]


def download_book(book: int, session: requests.Session, min_words: int) -> str:
    """Fetch and clean one book; raise if it is implausibly short."""
    page = PAGE_TEMPLATE.format(roman=roman_for(book))
    logger.info("fetching %s", page)
    text = html_to_text(fetch_rendered_html(page, session))

    words = word_count(text)
    if words < min_words:
        raise DownloadError(
            f"Book {roman_for(book)} ({page}) produced only {words} words "
            f"(minimum {min_words}). This is a scrape failure, not a short book - "
            "the page layout may have changed, or the request was blocked. "
            "Nothing was written."
        )
    return text


def download_all(
    books: Sequence[int],
    out_dir: Path,
    min_words: int = MIN_WORDS,
    force: bool = False,
    sleep_seconds: float = SLEEP_SECONDS,
) -> dict[int, Path]:
    """Download every requested book, then write them. Raises on any failure.

    Files are written only after **all** requested books pass the word-count
    check, so a failure halfway through cannot leave a half-built corpus behind.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    texts: dict[int, str] = {}
    for index, book in enumerate(books):
        target = out_dir / f"book_{book}.txt"
        if target.exists() and not force:
            logger.info("book_%d.txt exists; skipping (use --force to refetch)", book)
            continue
        if index:
            time.sleep(sleep_seconds)
        texts[book] = download_book(book, session, min_words)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[int, Path] = {}
    for book, text in texts.items():
        target = out_dir / f"book_{book}.txt"
        target.write_text(text, encoding="utf-8")
        written[book] = target
    return written


def report(out_dir: Path, books: Sequence[int]) -> int:
    """Print per-book word counts and flag anything suspicious."""
    print(f"\n{'book':>6}  {'words':>7}  {'chapters':>8}  file")
    total = 0
    largest = (0, 0)
    for book in books:
        path = out_dir / f"book_{book}.txt"
        if not path.exists():
            print(f"{book:>6}  {'MISSING':>7}")
            continue
        text = path.read_text(encoding="utf-8")
        words = word_count(text)
        total += words
        if words > largest[1]:
            largest = (book, words)
        print(f"{book:>6}  {words:>7}  {len(chapter_headings(text)):>8}  {path.name}")

    print(f"{'total':>6}  {total:>7}")
    print(f"\nlargest book: {largest[0]} ({largest[1]} words) - expected Book II")
    if largest[0] != 2:
        print("WARNING: Book II is normally the largest; check the scrape.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--books",
        nargs="*",
        type=int,
        default=list(range(1, len(ROMAN_NUMERALS) + 1)),
        help="book numbers to fetch (default: 1-15)",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--min-words", type=int, default=MIN_WORDS)
    parser.add_argument("--force", action="store_true", help="refetch books already on disk")
    parser.add_argument("--sleep", type=float, default=SLEEP_SECONDS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    out_dir = Path(args.out_dir)
    try:
        written = download_all(
            args.books,
            out_dir,
            min_words=args.min_words,
            force=args.force,
            sleep_seconds=args.sleep,
        )
    except (DownloadError, requests.RequestException) as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1

    logger.info("wrote %d file(s) to %s", len(written), out_dir)
    return report(out_dir, args.books)


if __name__ == "__main__":
    sys.exit(main())
