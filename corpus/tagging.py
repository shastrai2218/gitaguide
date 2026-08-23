"""Weak labelling of verses with themes, emotions and concepts.

Research-pipeline role
---------------------
The knowledge graph needs ``(:Verse)-[:ADDRESSES_EMOTION]->(:Emotion)`` and
``(:Verse)-[:HAS_THEME]->(:Theme)`` edges, but neither the Gita JSON nor the
Wikisource Arthashastra ships with such labels. This module produces them by
keyword rule, i.e. **weak supervision**: cheap, deterministic, auditable, and
good enough to build the graph.

Limitations to state in the dissertation: rules over English translations miss
metaphor and mistag polysemy ("war" as inner conflict). The planned improvement
is a manually annotated gold subset (~200 verses) plus rule-vs-human agreement
(Cohen's kappa), which is future work.

Emotion labels are drawn from the GoEmotions taxonomy so they line up exactly
with the output of :mod:`app.pipeline.emotion`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: ``theme -> cue patterns``
THEME_RULES: dict[str, tuple[str, ...]] = {
    "duty": (r"\bduty|dharma|obligation|ordained|prescribed|assigned work\b",),
    "detachment": (r"\bdetach|renounc|non-?attach|without desire|give up (?:the )?fruit",),
    "action": (r"\baction|karma|work|deed|act\b",),
    "equanimity": (r"\bequal|even-?minded|equanimity|balanced|steady mind|same in\b",),
    "impermanence": (r"\bimpermanen|transient|comes? and goes?|never (?:was|is) not|perish",),
    "grief": (r"\bgrieve|grief|mourn|lament|sorrow\b",),
    "fear": (r"\bfear|afraid|dread|terror\b",),
    "anger": (r"\banger|wrath|rage|krodha\b",),
    "desire": (r"\bdesire|craving|lust|kama|longing\b",),
    "knowledge": (r"\bknowledge|wisdom|jnana|discriminat|understand\b",),
    "devotion": (r"\bdevot|bhakti|worship|surrender|refuge\b",),
    "meditation": (r"\bmeditat|dhyana|yoga of|concentrat|seated\b",),
    "self_mastery": (r"\bself-?control|restrain|discipline|subdue|conquer the mind\b",),
    "self_effort": (r"\blift (?:him|your)self|own effort|raise (?:him|your)self\b",),
    "leadership": (r"\bking|ruler|sovereign|minister|commander|leader\b",),
    "governance": (r"\bgovern|administ|state|kingdom|policy|treasury|revenue\b",),
    "strategy": (r"\bstrategy|policy|alliance|war|peace|treaty|spy|enemy\b",),
    "accountability": (r"\baudit|account|examin|inspect|superintend|embezzl",),
    "hiring": (r"\bappoint|counsell?or|servant|employ|officer\b",),
    "time_management": (r"\bdivide the (?:day|night)|part of the day|schedule|watch\b",),
    "wealth": (r"\bwealth|money|profit|gold|treasure|artha\b",),
    "conflict": (r"\bconflict|quarrel|dispute|battle|fight|war\b",),
    "relationships": (r"\bfriend|enemy|kinsm|family|wife|husband|son\b",),
    "career": (r"\boccupation|livelihood|profession|vocation|one'?s own work\b",),
    "peace": (r"\bpeace|tranquil|calm|shanti\b",),
    "ethics": (r"\brighteous|virtue|sin|wrong|just|unjust|honest\b",),
}

#: ``GoEmotions label -> cue patterns``
EMOTION_RULES: dict[str, tuple[str, ...]] = {
    "grief": (r"\bgrieve|grief|mourn|lament|dead|death|slain\b",),
    "sadness": (r"\bsorrow|sad|despond|dejected|misery|wretched\b",),
    "fear": (r"\bfear|afraid|dread|terror|danger|peril\b",),
    "nervousness": (r"\banxiet|anxious|doubt|restless|agitat|uncertain|waver",),
    "anger": (r"\banger|wrath|rage|hatred|hostil|resent",),
    "annoyance": (r"\birritat|vex|provok|offend",),
    "confusion": (r"\bconfus|bewilder|delusion|perplex|which .* better|two minds",),
    "disappointment": (r"\bfail|defeat|loss|disgrace|dishonour|dishonor",),
    "remorse": (r"\bsin|guilt|repent|remorse|shame\b",),
    "disgust": (r"\bcorrupt|filth|abhor|loath|disgust",),
    "desire": (r"\bdesire|craving|greed|lust|ambition",),
    "pride": (r"\bpride|vanity|arrogan|conceit",),
    "relief": (r"\bpeace|relief|freed|liberat|calm",),
    "optimism": (r"\bhope|prosper|success|victory|attain",),
    "love": (r"\blove|affection|dear|compassion",),
    "gratitude": (r"\bgrateful|thank|gratitude",),
    "curiosity": (r"\bask|question|enquir|inquir|tell me",),
}

#: ``concept -> cue patterns`` (Sanskrit technical vocabulary worth its own node)
CONCEPT_RULES: dict[str, tuple[str, ...]] = {
    "nishkama_karma": (r"\bwithout (?:attachment|desire) (?:to|for) (?:the )?fruit|not .* fruits",),
    "karma_yoga": (r"\byoga of action|karma yoga|path of action",),
    "svadharma": (r"\bown duty|own work|svadharma|one'?s own (?:law|duty)",),
    "atman": (r"\bself\b|soul|atman|indestructible|eternal",),
    "moksha": (r"\bliberat|moksha|freedom from|final beatitude",),
    "gunas": (r"\bsattva|rajas|tamas|three (?:modes|qualities)",),
    "shadgunya": (r"\bsix (?:measures|forms) of policy|peace, war",),
    "danda": (r"\bpunish|rod|danda|coerc",),
    "rajadharma": (r"\bduty of (?:a|the) king|kingly duty|rajadharma",),
    "artha": (r"\bwealth|artha|material gain|economic",),
}

_COMPILED: dict[str, dict[str, tuple[re.Pattern[str], ...]]] = {
    "themes": {
        label: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        for label, patterns in THEME_RULES.items()
    },
    "emotions": {
        label: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        for label, patterns in EMOTION_RULES.items()
    },
    "concepts": {
        label: tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        for label, patterns in CONCEPT_RULES.items()
    },
}


def _match_labels(text: str, group: str, limit: int) -> list[str]:
    hits: list[tuple[str, int]] = []
    for label, patterns in _COMPILED[group].items():
        score = sum(len(pattern.findall(text)) for pattern in patterns)
        if score:
            hits.append((label, score))
    hits.sort(key=lambda item: (-item[1], item[0]))
    return [label for label, _ in hits[:limit]]


def tag_themes(text: str, limit: int = 5) -> list[str]:
    """Themes cued by ``text`` (most frequent cue first)."""
    return _match_labels(text, "themes", limit)


def tag_emotions(text: str, limit: int = 4) -> list[str]:
    """GoEmotions-compatible labels the passage speaks to."""
    return _match_labels(text, "emotions", limit)


def tag_concepts(text: str, limit: int = 3) -> list[str]:
    """Sanskrit technical concepts mentioned."""
    return _match_labels(text, "concepts", limit)


def tag_all(text: str) -> dict[str, list[str]]:
    """Convenience wrapper returning all three label sets."""
    return {
        "themes": tag_themes(text),
        "emotions_addressed": tag_emotions(text),
        "concepts": tag_concepts(text),
    }


def coverage_report(texts: Iterable[str]) -> dict[str, float]:
    """Fraction of passages that received at least one label of each kind.

    Printed by both corpus builders so a low-coverage rule set is obvious
    instead of silently producing a graph with no emotion edges.
    """
    total = 0
    with_theme = with_emotion = with_concept = 0
    for text in texts:
        total += 1
        if tag_themes(text):
            with_theme += 1
        if tag_emotions(text):
            with_emotion += 1
        if tag_concepts(text):
            with_concept += 1
    if total == 0:
        return {"themes": 0.0, "emotions": 0.0, "concepts": 0.0}
    return {
        "themes": round(with_theme / total, 3),
        "emotions": round(with_emotion / total, 3),
        "concepts": round(with_concept / total, 3),
    }
