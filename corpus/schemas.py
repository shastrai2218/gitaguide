"""Canonical corpus schema shared by the loaders, the graph builder and tests.

Research-pipeline role
---------------------
This is the single source of truth for what a "unit of scripture" is.  Both the
Bhagavad Gita loader (:mod:`corpus.load_gita`) and the Arthashastra parser
(:mod:`corpus.build_arthashastra`) emit :class:`Verse` objects, so downstream
stages (KG builder, retriever, generator, evaluation) never need to know which
text a passage came from beyond its :class:`ScriptureSource` tag.

Verse ID convention
-------------------
* Bhagavad Gita : ``BG-<chapter>.<verse>``            e.g. ``BG-2.47``
* Arthashastra  : ``AS-<book>.<chapter>.<sutra>``     e.g. ``AS-1.2.3``
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from enum import Enum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

BG_ID_RE = re.compile(r"^BG-(\d+)\.(\d+(?:-\d+)?)$")
AS_ID_RE = re.compile(r"^AS-(\d+)\.(\d+)\.(\d+)$")
VERSE_ID_RE = re.compile(r"\b((?:BG|AS)-\d+(?:\.\d+(?:-\d+)?){1,2})\b")


class ScriptureSource(str, Enum):
    """Which corpus a verse belongs to."""

    BHAGAVAD_GITA = "bhagavad_gita"
    ARTHASHASTRA = "arthashastra"

    @property
    def id_prefix(self) -> str:
        return "BG" if self is ScriptureSource.BHAGAVAD_GITA else "AS"

    @property
    def display_name(self) -> str:
        return "Bhagavad Gita" if self is ScriptureSource.BHAGAVAD_GITA else "Arthashastra"


class Verse(BaseModel):
    """One retrievable passage of scripture.

    Attributes
    ----------
    id:
        Stable citation key (``BG-2.47`` / ``AS-1.2.3``). Used verbatim in the
        generated answer so every claim is traceable.
    themes:
        Human-readable concept labels -> become ``Theme`` nodes in the KG.
    emotions_addressed:
        GoEmotions-compatible labels -> become ``Emotion`` nodes, and are the
        bridge between the emotion detector and graph retrieval.
    """

    id: str
    source: ScriptureSource
    chapter: int = Field(ge=0)
    verse: int = Field(ge=0)
    book: int | None = Field(default=None, ge=0, description="Arthashastra book number")
    text_sanskrit: str = ""
    text_english: str
    transliteration: str = ""
    commentary: str = ""
    themes: list[str] = Field(default_factory=list)
    emotions_addressed: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not (BG_ID_RE.match(value) or AS_ID_RE.match(value)):
            raise ValueError(
                f"invalid verse id {value!r}; expected BG-<ch>.<v> or AS-<bk>.<ch>.<sutra>"
            )
        return value

    @field_validator("themes", "emotions_addressed", "concepts")
    @classmethod
    def _normalise_labels(cls, value: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for item in value:
            cleaned = item.strip().lower().replace(" ", "_")
            if cleaned:
                seen.setdefault(cleaned, None)
        return list(seen)

    @model_validator(mode="after")
    def _check_id_matches_source(self) -> Self:
        if self.source is ScriptureSource.ARTHASHASTRA and self.book is None:
            raise ValueError("Arthashastra verses must carry a book number")
        if not self.id.startswith(self.source.id_prefix + "-"):
            raise ValueError(f"id {self.id!r} does not match source {self.source.value!r}")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def citation(self) -> str:
        """Human-facing citation, e.g. ``Bhagavad Gita 2.47 (BG-2.47)``."""
        if self.source is ScriptureSource.BHAGAVAD_GITA:
            locus = f"{self.chapter}.{self.verse}"
        else:
            locus = f"Book {self.book}, Chapter {self.chapter}, Sutra {self.verse}"
        return f"{self.source.display_name} {locus} ({self.id})"

    def embedding_text(self) -> str:
        """Text handed to the embedder / vector index.

        Themes and emotions are appended so that the vector space carries a
        little of the graph's semantics too (cheap late fusion).
        """
        parts = [self.text_english.strip()]
        if self.themes:
            parts.append("Themes: " + ", ".join(self.themes))
        if self.emotions_addressed:
            parts.append("Addresses: " + ", ".join(self.emotions_addressed))
        return "\n".join(parts)


class Corpus(BaseModel):
    """A serialisable bag of verses with convenience accessors."""

    verses: list[Verse] = Field(default_factory=list)

    def __iter__(self) -> Iterator[Verse]:  # type: ignore[override]
        return iter(self.verses)

    def __len__(self) -> int:
        return len(self.verses)

    def by_id(self, verse_id: str) -> Verse | None:
        return next((v for v in self.verses if v.id == verse_id), None)

    def by_source(self, source: ScriptureSource) -> list[Verse]:
        return [v for v in self.verses if v.source is source]

    def extend(self, verses: Iterable[Verse]) -> None:
        known = {v.id for v in self.verses}
        for verse in verses:
            if verse.id not in known:
                self.verses.append(verse)
                known.add(verse.id)

    # ------------------------------------------------------------------- io
    @classmethod
    def load(cls, path: str | Path) -> Corpus:
        """Load a corpus from a JSON file (list of verses or ``{"verses": []}``)."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = {"verses": raw}
        return cls.model_validate(raw)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target


def make_gita_id(chapter: int, verse: int | str) -> str:
    return f"BG-{chapter}.{verse}"


def make_arthashastra_id(book: int, chapter: int, sutra: int) -> str:
    return f"AS-{book}.{chapter}.{sutra}"


def extract_verse_ids(text: str) -> list[str]:
    """Pull every citation-shaped token out of free text (used by the
    citation-enforcement step in :mod:`app.pipeline.generation`)."""
    seen: dict[str, None] = {}
    for match in VERSE_ID_RE.finditer(text):
        seen.setdefault(match.group(1), None)
    return list(seen)
