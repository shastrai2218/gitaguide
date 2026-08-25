"""Build the Arthashastra half of the corpus from the Shamasastry (1915) text.

Research-pipeline role
---------------------
The Gita is available as clean JSON; the Arthashastra is not. The public-domain
R. Shamasastry translation (1915) exists on Wikisource as prose split into Books
and Chapters, so this module turns that prose into addressable units:

    Book  -> ``BOOK I``, ``Book 1``, roman or arabic
    Chapter -> ``CHAPTER I``, ``Chapter 2``
    Sutra -> one paragraph (or sentence group), numbered from 1 within a chapter

giving ids of the form ``AS-1.2.3`` (book 1, chapter 2, sutra 3). Paragraph
segmentation is an editorial decision, not a philological one: sutra numbering
here is *positional*, and the README says so, because retrieval only needs
stable, citable addresses.

Usage
-----
::

    python -m corpus.build_arthashastra --input book1.txt
    python -m corpus.build_arthashastra --url "<wikisource raw url>"
    python -m corpus.build_arthashastra --offline        # bundled sample
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path

from app.config import CORPUS_DATA_DIR, PROJECT_ROOT, SAMPLE_CORPUS_PATH
from corpus.schemas import Corpus, ScriptureSource, Verse, make_arthashastra_id
from corpus.tagging import coverage_report, tag_all

logger = logging.getLogger(__name__)

#: Wikisource serves raw wikitext with ``?action=raw``.
DEFAULT_URL = (
    "https://en.wikisource.org/wiki/Arthashastra/Book_I?action=raw"
)

BOOK_RE = re.compile(r"^\s*BOOK\s+([IVXLC\d]+)\b.*$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+([IVXLC\d]+)\b.*$", re.IGNORECASE)
#: Wikitext leftovers to strip before segmentation.
WIKI_MARKUP_RE = re.compile(
    r"(\{\{[^}]*\}\}|\[\[[^\]|]*\|?|\]\]|<ref[^>]*>.*?</ref>|<[^>]+>|^=+.*?=+$)",
    re.MULTILINE | re.DOTALL,
)
FOOTNOTE_RE = re.compile(r"\[\d+\]")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

#: Where ``scripts/download_arthashastra.py`` writes ``book_1.txt``..``book_15.txt``.
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "arthashastra_raw"
#: One file per book; the number in the filename is authoritative for the id.
BOOK_FILE_RE = re.compile(r"^book_(\d+)\.txt$", re.IGNORECASE)

#: Paragraphs shorter than this are treated as headings/noise.
MIN_SUTRA_CHARS = 60
#: Very long paragraphs are split on sentence boundaries near this length.
MAX_SUTRA_CHARS = 900


def roman_to_int(value: str) -> int:
    """``'IV' -> 4``; passes plain digits straight through."""
    value = value.strip().upper()
    if value.isdigit():
        return int(value)
    total = 0
    previous = 0
    for char in reversed(value):
        current = ROMAN_VALUES.get(char, 0)
        total += current if current >= previous else -current
        previous = max(previous, current)
    return total or 1


def fetch_text(url: str, timeout: int = 30) -> str:
    """Download the source text (wikitext or plain)."""
    logger.info("downloading %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": "GitaGuide/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"could not fetch {url}: {exc}") from exc


def clean_text(raw: str) -> str:
    """Strip wiki markup, footnote markers and collapse whitespace."""
    text = WIKI_MARKUP_RE.sub(" ", raw)
    text = FOOTNOTE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_long_paragraph(paragraph: str, max_chars: int = MAX_SUTRA_CHARS) -> list[str]:
    """Split an over-long paragraph on sentence boundaries."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = re.split(r"(?<=[.;:])\s+", paragraph)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def iter_sutras(
    text: str,
    default_book: int = 1,
    force_book: bool = False,
) -> Iterator[tuple[int, int, int, str]]:
    """Yield ``(book, chapter, sutra, text)`` tuples from the cleaned prose.

    ``force_book`` pins the book number to ``default_book`` and ignores any
    ``BOOK ...`` line in the text. Directory mode uses it because the filename
    (``book_7.txt``) is authoritative: several Wikisource book pages open with a
    title line naming the book in prose, and a stray match there would silently
    mis-number every id in the file.
    """
    book = default_book
    chapter = 0
    sutra = 0

    for block in text.split("\n"):
        line = block.strip()
        if not line:
            continue

        book_match = None if force_book else BOOK_RE.match(line)
        if book_match:
            book = roman_to_int(book_match.group(1))
            chapter = 0
            sutra = 0
            continue

        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            chapter = roman_to_int(chapter_match.group(1))
            sutra = 0
            continue

        if chapter == 0:
            # Front matter before the first chapter heading.
            continue
        if len(line) < MIN_SUTRA_CHARS:
            continue

        for chunk in split_long_paragraph(line):
            sutra += 1
            yield book, chapter, sutra, chunk


def build_corpus_from_text(
    text: str,
    default_book: int = 1,
    limit: int | None = None,
    force_book: bool = False,
) -> Corpus:
    """Parse cleaned prose into schema-valid :class:`Verse` objects."""
    verses: list[Verse] = []
    for book, chapter, sutra, body in iter_sutras(clean_text(text), default_book, force_book):
        labels = tag_all(body)
        verses.append(
            Verse(
                id=make_arthashastra_id(book, chapter, sutra),
                source=ScriptureSource.ARTHASHASTRA,
                book=book,
                chapter=chapter,
                verse=sutra,
                text_english=body,
                themes=labels["themes"],
                emotions_addressed=labels["emotions_addressed"],
                concepts=labels["concepts"],
            )
        )
        if limit is not None and len(verses) >= limit:
            break
    logger.info("parsed %d Arthashastra sutras", len(verses))
    return Corpus(verses=verses)


def load_arthashastra_dir(directory: str | Path, limit: int | None = None) -> Corpus:
    """Parse a directory of ``book_<n>.txt`` files into one corpus.

    This is the normal path: ``scripts/download_arthashastra.py`` writes one file
    per book, and the book number comes from the filename rather than from the
    text (see ``force_book`` in :func:`iter_sutras`).
    """
    path = Path(directory)
    if not path.is_dir():
        raise FileNotFoundError(f"no such directory: {path}")

    files: list[tuple[int, Path]] = []
    for candidate in path.iterdir():
        match = BOOK_FILE_RE.match(candidate.name)
        if match:
            files.append((int(match.group(1)), candidate))
    if not files:
        raise FileNotFoundError(
            f"{path} contains no book_<n>.txt files "
            "(run: python -m scripts.download_arthashastra)"
        )
    files.sort()

    corpus = Corpus()
    for book, file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        book_corpus = build_corpus_from_text(text, default_book=book, force_book=True)
        logger.info(
            "%s -> book %d: %d sutra(s), %d chapter(s)",
            file_path.name,
            book,
            len(book_corpus),
            len({verse.chapter for verse in book_corpus}),
        )
        if not len(book_corpus):
            raise ValueError(
                f"{file_path.name} produced no sutras - the cleaning and the parser "
                "disagree (expected 'CHAPTER ...' heading lines)"
            )
        corpus.extend(book_corpus)
        if limit is not None and len(corpus) >= limit:
            return Corpus(verses=list(corpus)[:limit])
    return corpus


def load_arthashastra(
    input_path: str | None = None,
    url: str | None = None,
    offline: bool = False,
    default_book: int = 1,
    limit: int | None = None,
    input_dir: str | Path | None = None,
) -> Corpus:
    """Load from a directory, a single file, a URL, or the bundled sample.

    Precedence: ``offline`` -> ``input_path`` -> ``input_dir`` (when it exists)
    -> ``url``. So a bare ``python -m corpus.build_arthashastra`` builds from
    ``data/arthashastra_raw/`` once it has been downloaded.
    """
    if offline:
        logger.info("offline mode: using bundled sample corpus")
        return _sample_arthashastra()

    if not input_path and input_dir and Path(input_dir).is_dir():
        return load_arthashastra_dir(input_dir, limit)

    if input_path:
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"no such file: {path}")
        return build_corpus_from_text(
            path.read_text(encoding="utf-8", errors="replace"), default_book, limit
        )

    try:
        raw = fetch_text(url or DEFAULT_URL)
    except RuntimeError:
        logger.exception("download failed; falling back to the bundled sample corpus")
        return _sample_arthashastra()

    corpus = build_corpus_from_text(raw, default_book, limit)
    if not len(corpus):
        logger.warning("parser produced nothing; falling back to the bundled sample")
        return _sample_arthashastra()
    return corpus


def _sample_arthashastra() -> Corpus:
    corpus = Corpus.load(SAMPLE_CORPUS_PATH)
    return Corpus(verses=corpus.by_source(ScriptureSource.ARTHASHASTRA))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse the Shamasastry Arthashastra into Book/Chapter/Sutra JSON"
    )
    parser.add_argument("--input", default=None, help="local text/wikitext file")
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_RAW_DIR),
        help="directory of book_<n>.txt files (default: data/arthashastra_raw)",
    )
    parser.add_argument("--url", default=None, help="source url (Wikisource ?action=raw)")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--book", type=int, default=1, help="book number if headings are absent")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(CORPUS_DATA_DIR / "arthashastra.json"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    corpus = load_arthashastra(
        input_path=args.input,
        url=args.url,
        offline=args.offline,
        default_book=args.book,
        limit=args.limit,
        input_dir=args.input_dir,
    )
    path = corpus.save(args.out)
    logger.info("wrote %d sutras to %s", len(corpus), path)
    logger.info("weak-label coverage: %s", coverage_report(v.text_english for v in corpus))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
