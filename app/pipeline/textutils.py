"""Small shared text helpers used by more than one pipeline stage.

Kept separate so :mod:`app.pipeline.safety` and :mod:`app.pipeline.intent` can
apply *identical* negation and context rules without importing each other. If
the gate suppresses a negated cue but the intent classifier does not, the model
layer fires anyway and the gate's precision measurement becomes meaningless -
this module is what keeps the two consistent.
"""

from __future__ import annotations

import re

#: Negation tokens that neutralise a following cue ("I am *not* suicidal").
NEGATION_RE = re.compile(
    r"\b(?:not|never|no|n't|nt|dont|don't|doesnt|doesn't|didnt|didn't|wont|won't|"
    r"wouldn't|isn't|aren't|neither|nor|hardly|rarely)\b",
    re.IGNORECASE,
)

#: How far back from a cue a negation token still counts (characters).
NEGATION_WINDOW_CHARS = 28

#: "I am asking about the topic", not "I am in it".
ACADEMIC_CONTEXT_RE = re.compile(
    r"(?:my (?:thesis|dissertation|paper|research|assignment)|"
    r"for (?:a|my) (?:class|course|project|exam)|"
    r"(?:red[- ]?team|test case|unit test|dataset|corpus)\b)",
    re.IGNORECASE,
)


#: Someone *else* is the subject of the cue ("my friend", "a colleague").
THIRD_PARTY_RE = re.compile(
    r"\b(?:my|a|his|her|their)\s+(?:friend|colleague|cousin|brother|sister|neighbour|"
    r"neighbor|classmate|student|patient|uncle|aunt)\b",
    re.IGNORECASE,
)

#: The episode is described as over ("recovered", "last year", "is fine now").
RECOVERY_RE = re.compile(
    r"\b(?:recovered|got better|is (?:fine|okay|ok) now|came out of it|"
    r"years? ago|last year|back then|used to be)\b",
    re.IGNORECASE,
)


def is_third_party_past(text: str) -> bool:
    """True when a cue is about another person *and* framed as resolved.

    Narrow on purpose. "My friend wants to kill himself" must still fire (the
    user needs the helplines either way); only a third-party subject **plus** an
    explicit recovery/past marker suppresses a cue, and only for cues marked
    negatable - never for an act in progress. Shared by the safety gate and the
    intent prior so both reach the same conclusion.
    """
    return bool(THIRD_PARTY_RE.search(text)) and bool(RECOVERY_RE.search(text))


def is_negated(text: str, match_start: int, window: int = NEGATION_WINDOW_CHARS) -> bool:
    """True when a negation token sits immediately before ``match_start``."""
    prefix = text[max(0, match_start - window) : match_start]
    return bool(NEGATION_RE.search(prefix))


def has_academic_framing(text: str) -> bool:
    """True when the message frames the topic as study material."""
    return bool(ACADEMIC_CONTEXT_RE.search(text))


def matches_unnegated(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match of ``pattern`` in ``text`` that is not negated."""
    for match in pattern.finditer(text):
        if not is_negated(text, match.start()):
            return match
    return None


def truncate(text: str, limit: int) -> str:
    """Trim to ``limit`` characters with an ellipsis (for logs and summaries)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
