"""How a cited verse is named and linked in a reply.

Tester item 4: *"Dont show direct slog directly just give it reference link."*
The reply now names the verse and points at it instead of pasting it, so the
label and the link are part of the contract rather than presentation detail,
and they live here so there is exactly one definition of each.

The link, and why it is a link and not text
-------------------------------------------
Item 10 asked for Prabhupada's *Bhagavad-gītā As It Is* as the Gita edition.
BBT's translations and purports are in copyright - the Delhi High Court granted
a permanent injunction on 5 April 2024 against "reproduction, storage in
electronic format, and public communication" of them - so GitaGuide does not
store, embed or transmit a word of that edition. It **links** to it, which is
not reproduction, and generates from the public-domain Purohit Swami
translation it already holds. That is decision (b′), recorded in
``docs/legal/``.

So a Gita citation carries a deep link to the authoritative rendering on
vedabase.io while the answer itself is grounded in the PD text. A tester who
doubts a rendering can check it in one click, against the edition they asked
for.

Arthashastra citations carry no link. Shamasastry's 1915 translation is public
domain and is the text we hold, but there is no canonical per-sutra URL worth
sending anyone to; Phase 2 adds ``source_url`` to the verse records and this
function will read it from the data rather than deriving it.
"""

from __future__ import annotations

from corpus.schemas import AS_ID_RE, BG_ID_RE, ScriptureSource

#: Deep-link template for the Gita, per item 10 / decision (b′).
VEDABASE_BG_URL = "https://vedabase.io/en/library/bg/{chapter}/{verse}"


def reference_label(verse_id: str) -> str:
    """The form a reference takes in a reply: ``BG 2.47``, ``AS 5.2.6``.

    The ids stay hyphenated everywhere the machinery touches them - the prompt,
    ``extract_verse_ids``, the citation check, the graph - because that is what
    ``VERSE_ID_RE`` matches. This is the human form, and it is applied at the
    edge only.

    >>> reference_label("BG-2.47")
    'BG 2.47'
    >>> reference_label("AS-5.2.6")
    'AS 5.2.6'
    """
    return verse_id.replace("-", " ", 1)


def reference_url(verse_id: str, source: ScriptureSource | str | None = None) -> str:
    """A URL for the verse, or ``""`` when there is nothing worth linking.

    Derived from the id rather than stored, for now: Phase 2 puts ``source_url``
    on every verse record and this becomes a lookup. Deriving it is safe
    because the id *is* chapter and verse.

    >>> reference_url("BG-2.47")
    'https://vedabase.io/en/library/bg/2/47'
    >>> reference_url("AS-5.2.6")
    ''
    >>> reference_url("nonsense")
    ''
    """
    if source is not None:
        value = source.value if isinstance(source, ScriptureSource) else str(source)
        if value != ScriptureSource.BHAGAVAD_GITA.value:
            return ""

    match = BG_ID_RE.match(verse_id)
    if match is None:
        return ""
    chapter, verse = match.group(1), match.group(2)
    return VEDABASE_BG_URL.format(chapter=chapter, verse=verse)


def is_known_verse_id(verse_id: str) -> bool:
    """True for a syntactically valid Gita or Arthashastra id.

    >>> is_known_verse_id("BG-18.78"), is_known_verse_id("AS-1.2.3")
    (True, True)
    >>> is_known_verse_id("BG-2")
    False
    """
    return bool(BG_ID_RE.match(verse_id) or AS_ID_RE.match(verse_id))
