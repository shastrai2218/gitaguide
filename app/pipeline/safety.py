"""Stage 3 - the safety gate (runs BEFORE retrieval and generation).

Research-pipeline role
---------------------
GitaGuide answers emotionally loaded questions, so the gate is the component
that decides whether the system is even allowed to give scriptural guidance for
this turn.  It has three layers:

``rule layer``    keyword/regex detection of suicidal ideation, self-harm, harm
                  to others, abuse/violence and medical emergencies.  Tuned for
                  **recall**: a false positive costs a helpline message, a false
                  negative is unacceptable (see ``eval/test_safety_redteam.py``).
``risk policy``   applies the semantic crisis score from
                  :mod:`app.pipeline.intent`. Emotion is context only and never
                  blocks independently.
``output layer``  runs AFTER generation on the produced answer, catching unsafe
                  content the LLM may have introduced (harm methods, telling the
                  user to stop treatment, moralising about their suffering).

When the input layers trip, the orchestrator short-circuits: retrieval and
generation never run, and the compassionate crisis response below is returned
verbatim.  Every trigger is logged through a :class:`SafetyEventSink` so the
dissertation can report gate precision/recall on real traffic.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from re import Pattern

from app.config import Settings, get_settings
from app.pipeline.schemas import (
    EmotionResult,
    IntentLabel,
    IntentResult,
    SafetyCategory,
    SafetyLayer,
    SafetyTrigger,
    SafetyVerdict,
    SemanticStatus,
)
from app.pipeline.textutils import (
    has_academic_framing,
    is_negated,
    is_third_party_past,
)

logger = logging.getLogger(__name__)

# A semantic score below the blocking threshold is only audit-worthy once it is
# large enough to represent a substantive model signal. This is deliberately
# not a blocking threshold and must be calibrated from tester traffic.
SEMANTIC_NEAR_MISS_FLOOR = 0.20

# --------------------------------------------------------------------------- #
# Crisis response
# --------------------------------------------------------------------------- #
INDIAN_HELPLINES: tuple[tuple[str, str, str], ...] = (
    ("Tele-MANAS", "14416 (or 1-800-891-4416)", "Government of India, 24x7, many languages"),
    ("iCall (TISS)", "9152987821", "Mon-Sat, 10am-8pm, counsellor-staffed"),
    ("AASRA", "+91 98204 66726", "24x7 emotional support"),
    ("KIRAN", "1800-599-0019", "24x7 mental-health rehabilitation helpline"),
    ("Emergency services", "112", "If you are in immediate physical danger"),
)

CRISIS_RESPONSE_HEADER = (
    "I hear how much pain you are carrying right now, and I am glad you said it out loud.\n\n"
    "What you are describing is heavier than a verse can carry on its own, so I am going to "
    "stop and stay here with you instead of quoting scripture.\n\n"
    "Please reach a person who can be with you right now:"
)

CRISIS_RESPONSE_FOOTER = (
    "You do not have to explain everything to them - saying "
    "\"I am not safe right now\" is enough to start.\n\n"
    "If you are in immediate danger, please call 112 or go to the nearest hospital, "
    "and if you can, tell one person nearby so you are not alone tonight.\n\n"
    "I am still here. When you feel steadier and you want to talk about what led here - "
    "the work, the loss, the exhaustion - I can walk through it with you then."
)


def build_crisis_response(
    categories: Sequence[SafetyCategory] = (),
    include_footer: bool = True,
) -> str:
    """Compose the crisis message. Deliberately contains **no verse citations**.

    Parameters
    ----------
    categories:
        Used only to add one situation-specific line; the helpline block is
        always identical so it can be audited.
    """
    lines = [CRISIS_RESPONSE_HEADER, ""]
    for name, number, note in INDIAN_HELPLINES:
        lines.append(f"- **{name}** - {number} ({note})")
    lines.append("")

    if SafetyCategory.ABUSE_OR_VIOLENCE in categories:
        lines.append(
            "Because you mentioned someone hurting you: the Women Helpline (181) and "
            "Childline (1098) can help with immediate safety and shelter, and none of "
            "this is your fault.\n"
        )
    if SafetyCategory.HARM_TO_OTHERS in categories:
        lines.append(
            "If you are afraid of what you might do to someone else, please put distance "
            "between you and them and call one of the numbers above before you act.\n"
        )
    if SafetyCategory.MEDICAL_EMERGENCY in categories:
        lines.append(
            "If you have already taken something or you are physically hurt, call 112 or "
            "go to an emergency room now - that comes first.\n"
        )

    if include_footer:
        lines.append(CRISIS_RESPONSE_FOOTER)
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# Rule layer
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SafetyRule:
    """One regex rule in the deterministic layer.

    ``negatable`` marks rules where an explicit negation ("I am *not* suicidal")
    is a realistic phrasing that should not fire.  Rules describing an act in
    progress are never negatable - recall wins there.
    """

    name: str
    category: SafetyCategory
    pattern: Pattern[str]
    detail: str
    negatable: bool = False
    weight: float = 1.0


def _rule(
    name: str,
    category: SafetyCategory,
    pattern: str,
    detail: str,
    negatable: bool = False,
    weight: float = 1.0,
) -> SafetyRule:
    return SafetyRule(
        name=name,
        category=category,
        pattern=re.compile(pattern, re.IGNORECASE),
        detail=detail,
        negatable=negatable,
        weight=weight,
    )


RULES: tuple[SafetyRule, ...] = (
    # ---- suicidal ideation ------------------------------------------------
    _rule(
        "suicide_explicit",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:kill(?:ing)? myself|killing my self|end(?:ing)? my life|take my own life|"
        r"took my own life|commit(?:ting)? suicide|attempt(?:ed|ing)? suicide|"
        r"hang myself|hanging myself|jump off (?:a|the) (?:building|bridge|roof|terrace)|"
        r"shoot myself|drown myself|slit my wrists?)\b",
        "explicit statement of suicidal action",
    ),
    _rule(
        "suicide_terms",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:suicidal|suicide|kms|unalive)\b",
        "suicide-related vocabulary",
        negatable=True,
    ),
    _rule(
        "wish_to_die",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:want(?:s|ing)? to die|wish i (?:was|were) dead|wish i had never been born|"
        r"do(?:n't| not) want to (?:live|be here|wake up)|"
        r"do(?:n't| not) see (?:the |any )?point in living|"
        r"better off dead|world would be better without me|"
        r"no (?:reason|point) (?:to|in) (?:living|live|going on)|"
        r"can(?:'t|not) (?:go on|do this) any ?more|ready to give up on life|"
        r"end(?:ing)? it all|end(?:ing)? things tonight)\b",
        "expressed wish to die or to stop living",
        negatable=True,
    ),
    _rule(
        "perceived_burdensomeness",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:everyone|everybody|people|my family|they) (?:would|would all|'d) "
        r"be better off without me\b",
        "user believes other people would be better off without them",
        negatable=True,
    ),
    _rule(
        "wish_not_to_be_alive",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:do(?:n't| not|nt) (?:want|wanna) to be alive|"
        r"do(?:n't| not|nt) wanna be alive|"
        r"no (?:reason|point) (?:to|in) (?:be|being|stay|staying) alive|"
        r"no (?:reason|point) to live)\b",
        "expressed wish not to be alive",
        negatable=True,
    ),
    _rule(
        "hinglish_not_want_to_be_alive",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:(?:main |mai |mujhe )?(?:ab )?jeena nahi chaht[aiu]|"
        r"(?:main |mai |mujhe )?(?:ab )?zinda nahi rehna chaht[aiu]|"
        r"(?:main |mai |mujhe )?(?:ab )?zinda nahi rehna hai)\b",
        "Hinglish expression of not wanting to be alive",
        negatable=True,
    ),
    _rule(
        "hinglish_wish_not_to_live",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:(?:mujhe (?:ab )?|mera )?jeene ka mann nahi (?:hai|karta)|"
        r"mujhe (?:ab )?jeena nahi (?:hai|chahta|chahti))\b",
        "Hinglish expression of not wanting to live",
    ),
    _rule(
        "hinglish_end_everything",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\bab bas bahut ho gaya\b[^.!?]{0,40}\bmain khatam kar "
        r"(?:dunga|dungi) sab kuch\b",
        "Hinglish expression of ending everything after intolerable distress",
    ),
    _rule(
        "permanent_last_train",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\bcatch (?:the |my )?last train (?:tonight|today)[, ]+permanently\b",
        "farewell euphemism combining a final journey with permanence",
        negatable=True,
    ),
    _rule(
        "plan_or_means",
        SafetyCategory.SUICIDAL_IDEATION,
        r"\b(?:wrote (?:my|a) (?:suicide )?note|left a note for my family|"
        r"saying goodbye (?:to everyone|forever)|last (?:night|day) on earth|"
        r"bought (?:a rope|pills|poison)|stockpil(?:ed|ing) pills|"
        r"planned how (?:i|to) (?:will |would )?(?:die|do it))\b",
        "indication of a plan, means or goodbye",
    ),
    # ---- self harm --------------------------------------------------------
    _rule(
        "self_harm",
        SafetyCategory.SELF_HARM,
        r"\b(?:self[- ]?harm(?:ing)?|cut(?:ting)? myself|burn(?:ing)? myself|"
        r"hurt(?:ing)? myself|punish(?:ing)? my body|starv(?:e|ing) myself|"
        r"hit(?:ting)? myself)\b",
        "self-harm behaviour",
    ),
    # ---- harm to others ---------------------------------------------------
    _rule(
        "harm_others",
        SafetyCategory.HARM_TO_OTHERS,
        r"\b(?:kill (?:him|her|them|my (?:boss|wife|husband|father|mother|colleague))|"
        r"murder (?:him|her|them)|stab (?:him|her|them)|shoot (?:him|her|them|up)|"
        r"make them (?:pay with|bleed)|hurt (?:him|her|them) badly|"
        r"burn (?:his|her|their) house)\b",
        "threat of violence toward another person",
    ),
    # ---- abuse / being harmed --------------------------------------------
    _rule(
        "abuse_victim",
        SafetyCategory.ABUSE_OR_VIOLENCE,
        r"\b(?:(?:he|she|they|my (?:husband|wife|father|mother|partner|boss|in[- ]?laws?)) "
        r"(?:beats?|beat|hits?|hit|hurts?|chokes?|threatens? to kill) me|"
        r"being (?:abused|assaulted|molested)|domestic (?:violence|abuse)|"
        r"i was (?:raped|assaulted|molested)|"
        r"threatens? to kill me)\b",
        "user reports being harmed by someone",
    ),
    # ---- medical emergency ------------------------------------------------
    _rule(
        "medical_emergency",
        SafetyCategory.MEDICAL_EMERGENCY,
        r"(?:\bi (?:just )?(?:took|swallowed|have taken)\b[^.!?]{0,40}?"
        r"\b(?:pills|tablets|poison|medicines?|medication)\b|"
        r"\boverdos(?:e|ed|ing)\b|\bi am bleeding\b|"
        r"\bcan(?:'t|not) (?:breathe|stop the bleeding)\b)",
        "possible medical emergency in progress",
    ),
)

# Categories that should never be softened by negation heuristics.
HARD_CATEGORIES = frozenset(
    {
        SafetyCategory.MEDICAL_EMERGENCY,
        SafetyCategory.SELF_HARM,
        SafetyCategory.HARM_TO_OTHERS,
    }
)


# --------------------------------------------------------------------------- #
# Output layer
# --------------------------------------------------------------------------- #
#: Crisis-adjacent vocabulary for the conservative fallback.
#:
#: This is **not** a detection layer. It is consulted only when the semantic
#: classifier has failed to produce a usable result *twice* and no deterministic
#: rule fired - a state in which the gate would otherwise be running on the rule
#: table alone. In that state the right default for a message that talks about
#: dying, being alive, or ending things is to stop, not to guess.
#:
#: Deliberately narrow-ish but recall-biased: a false positive here costs one
#: person an unnecessary helpline message on roughly 0.2% of turns; a false
#: negative is the failure recorded in docs/kg_measurements.md section 11.
#:
#: The negation / academic / third-party suppressors are **not** applied on this
#: path, and that is the point: those exist to keep precision high when the
#: model layer is working. When it is blind, precision is the wrong thing to
#: optimise.
SOFT_CRISIS_CUES: tuple[Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # English
        r"\b(?:die|dying|died|dead|death)\b",
        r"\b(?:alive|living|live)\b",
        r"\bend (?:it|things|my life|it all)\b",
        r"\b(?:suicide|suicidal|unalive|kms)\b",
        r"\b(?:self[- ]?harm|cut myself|overdose|od)\b",
        r"\b(?:give up|giving up|can(?:'t|not) go on|no point|no reason)\b",
        r"\b(?:worthless|hopeless|nothing left)\b",
        # Hinglish / Latin-script Hindi
        r"\b(?:jeena|jeene|zinda|zindagi)\b",
        r"\b(?:marna|mar jau|mar jaun|mar jaunga|mar jaungi|maut)\b",
        r"\b(?:khatam|khatm)\b",
    )
)


OUTPUT_RULES: tuple[SafetyRule, ...] = (
    _rule(
        "output_method_leak",
        SafetyCategory.UNSAFE_OUTPUT,
        r"\b(?:how to (?:kill|harm|hurt) (?:yourself|himself|herself)|"
        r"painless way to die|lethal dose|"
        r"you (?:should|could) (?:kill|harm|hurt) yourself)\b",
        "answer contains harm methods or encouragement",
    ),
    _rule(
        "output_treatment_interference",
        SafetyCategory.UNSAFE_OUTPUT,
        r"\b(?:stop (?:taking )?(?:your |the )?"
        r"(?:medication|medicines?|antidepressants|treatment|therapy)|"
        r"do(?:n't| not) (?:see|go to) (?:a |your )?(?:doctor|therapist|psychiatrist)|"
        r"you do(?:n't| not) need (?:therapy|medicine|a doctor))\b",
        "answer discourages medical care",
    ),
    _rule(
        "output_moralising_suffering",
        SafetyCategory.UNSAFE_OUTPUT,
        r"\b(?:you deserve (?:this|your) (?:suffering|pain|punishment)|"
        r"this is your karma for|it is your fault that you suffer|"
        r"you are being punished for)\b",
        "answer blames the user for their suffering",
    ),
    _rule(
        "output_dismissal",
        SafetyCategory.UNSAFE_OUTPUT,
        r"\b(?:just (?:get over it|move on|be happy)|"
        r"your (?:feelings|problems) (?:are|is) (?:not real|imaginary|trivial))\b",
        "answer dismisses the user's distress",
    ),
)

OUTPUT_FALLBACK_RESPONSE = (
    "I started to answer, but what I produced was not something I am willing to hand you - "
    "it strayed from the texts and toward advice that could do harm.\n\n"
    "What I can say plainly: what you are carrying is real, it is worth taking seriously, "
    "and it deserves a careful answer rather than a fast one. If it would help, tell me a "
    "little more about the situation - what changed recently, and what feels hardest right "
    "now - and I will work through the relevant verses with you.\n\n"
    "If things feel urgent, Tele-MANAS (14416) is free, 24x7 and staffed by people trained "
    "for exactly this."
)


# --------------------------------------------------------------------------- #
# Logging sink
# --------------------------------------------------------------------------- #
@dataclass
class SafetyEvent:
    """One auditable gate decision (persisted to ``safety_logs``)."""

    session_id: str | None
    message: str
    verdict: SafetyVerdict
    emotion_intensity: float = 0.0
    dominant_emotion: str = "neutral"
    intent: str = IntentLabel.OTHER.value
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SafetyEventSink(ABC):
    """Where gate triggers go. Swappable so tests can assert on them."""

    @abstractmethod
    def record(self, event: SafetyEvent) -> None: ...


class LoggingSafetyEventSink(SafetyEventSink):
    """Default sink: structured stdlib logging (always on)."""

    def record(self, event: SafetyEvent) -> None:
        if not event.verdict.requires_audit():
            return
        items = [*event.verdict.triggers, *event.verdict.signals]
        log = logger.warning if event.verdict.blocked else logger.info
        log(
            "SAFETY_AUDIT session=%s layers=%s categories=%s blocked=%s intensity=%.3f "
            "intent=%s items=%s",
            event.session_id,
            ",".join(event.verdict.audit_layers()),
            ",".join(event.verdict.audit_categories()),
            event.verdict.blocked,
            event.emotion_intensity,
            event.intent,
            ",".join(item.detail for item in items),
        )


class InMemorySafetyEventSink(SafetyEventSink):
    """Collects events in a list - used by the red-team suite and the fakes."""

    def __init__(self) -> None:
        self.events: list[SafetyEvent] = []

    def record(self, event: SafetyEvent) -> None:
        self.events.append(event)

    @property
    def triggered(self) -> list[SafetyEvent]:
        return [event for event in self.events if event.verdict.blocked]

    def clear(self) -> None:
        self.events.clear()


class CompositeSafetyEventSink(SafetyEventSink):
    """Fan out to several sinks (stdlib log + database, typically)."""

    def __init__(self, sinks: Iterable[SafetyEventSink]) -> None:
        self.sinks = list(sinks)

    def record(self, event: SafetyEvent) -> None:
        for sink in self.sinks:
            try:
                sink.record(event)
            except Exception:  # pragma: no cover - a sink must never break the gate
                logger.exception("safety sink %s failed", type(sink).__name__)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
class SafetyGate:
    """Rule layer + semantic risk policy on input, content check on output.

    Examples
    --------
    >>> gate = SafetyGate()
    >>> gate.check_input("I want to end my life tonight").blocked
    True
    >>> gate.check_input("Work has been stressful lately").blocked
    False
    """

    def __init__(
        self,
        settings: Settings | None = None,
        sink: SafetyEventSink | None = None,
        rules: Sequence[SafetyRule] | None = None,
        output_rules: Sequence[SafetyRule] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.sink = sink or LoggingSafetyEventSink()
        self.rules = tuple(rules) if rules is not None else RULES
        self.output_rules = tuple(output_rules) if output_rules is not None else OUTPUT_RULES

    # ------------------------------------------------------------ rule layer
    def _run_rules(
        self,
        text: str,
        rules: Sequence[SafetyRule],
        layer: SafetyLayer,
    ) -> list[SafetyTrigger]:
        triggers: list[SafetyTrigger] = []
        academic = has_academic_framing(text) or is_third_party_past(text)
        for rule in rules:
            match = rule.pattern.search(text)
            if not match:
                continue
            if (
                rule.negatable
                and rule.category not in HARD_CATEGORIES
                and (self._is_negated(text, match.start()) or academic)
            ):
                logger.debug("safety rule %s suppressed (negated/academic context)", rule.name)
                continue
            triggers.append(
                SafetyTrigger(
                    layer=layer,
                    category=rule.category,
                    detail=f"{rule.name}: {rule.detail}",
                    matched_text=match.group(0)[:120],
                    score=rule.weight,
                )
            )
        return triggers

    @staticmethod
    def _is_negated(text: str, match_start: int) -> bool:
        """True if a negation token sits just before the matched cue."""
        return is_negated(text, match_start)

    # ------------------------------------------------------------ risk policy
    def _run_risk_policy(
        self,
        emotion: EmotionResult | None,
        intent: IntentResult | None,
    ) -> tuple[list[SafetyTrigger], list[SafetyTrigger]]:
        """Return blocking triggers and non-blocking audit signals.

        The semantic crisis probability is the only model signal allowed to
        block. Emotion is attached as context for auditability but cannot block
        independently and does not lower the configured 0.5 threshold.
        """
        triggers: list[SafetyTrigger] = []
        signals: list[SafetyTrigger] = []

        if intent is None:
            return triggers, signals

        status = intent.semantic_status
        score = intent.semantic_crisis_score
        threshold = self.settings.safety_crisis_intent_threshold
        provenance = (
            f"provider={intent.semantic_provider or 'unknown'} "
            f"model={intent.semantic_model or 'unknown'}"
        )
        emotion_context = ""
        if emotion is not None and emotion.labels:
            emotion_context = (
                f"; emotion={emotion.dominant} intensity={emotion.intensity:.2f}"
            )

        if status is SemanticStatus.OK:
            if score >= threshold:
                triggers.append(
                    SafetyTrigger(
                        layer=SafetyLayer.MODEL,
                        category=SafetyCategory.SUICIDAL_IDEATION,
                        detail=(
                            f"semantic crisis score {score:.2f} >= {threshold:.2f}; "
                            f"{provenance}{emotion_context}"
                        ),
                        score=score,
                    )
                )
            elif score >= SEMANTIC_NEAR_MISS_FLOOR:
                signals.append(
                    SafetyTrigger(
                        layer=SafetyLayer.MODEL,
                        category=SafetyCategory.SUICIDAL_IDEATION,
                        detail=(
                            f"semantic crisis near-miss {score:.2f} < {threshold:.2f}; "
                            f"{provenance}{emotion_context}"
                        ),
                        score=score,
                    )
                )
        elif status in {SemanticStatus.UNAVAILABLE, SemanticStatus.MALFORMED}:
            signals.append(
                SafetyTrigger(
                    layer=SafetyLayer.MODEL,
                    category=SafetyCategory.NONE,
                    detail=(
                        f"semantic classifier {status.value} - risk UNKNOWN, not low; "
                        f"{provenance}{emotion_context}"
                    ),
                    score=0.0,
                )
            )

        return triggers, signals

    # ------------------------------------------------------- soft-cue fallback
    @staticmethod
    def _soft_cue(text: str) -> str | None:
        """The first crisis-adjacent cue in ``text``, or ``None``."""
        for pattern in SOFT_CRISIS_CUES:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return None

    def _run_soft_cue_fallback(
        self, text: str, intent: IntentResult | None, rule_fired: bool
    ) -> list[SafetyTrigger]:
        """Block a crisis-adjacent message when the semantic layer is blind.

        Policy, exactly:

        * the semantic result is malformed or unavailable **after** its retry,
          **and**
        * no deterministic rule fired, **and**
        * the message contains a soft cue

        -> serve the crisis response, logged under the ``soft_cue_fallback``
        layer so these decisions are countable and separable from real
        detections.

        A message with **no** cue and a failed parse keeps the previous
        behaviour: it proceeds, and the audit signal from the risk policy
        records that the classifier was blind. So a broken classifier can never
        leave a crisis-adjacent message single-layered, while ordinary turns are
        never blocked by a parse failure.

        Emotion is not consulted: GoEmotions never independently blocks.
        """
        if rule_fired or intent is None:
            return []
        if intent.semantic_status not in {SemanticStatus.MALFORMED, SemanticStatus.UNAVAILABLE}:
            return []

        cue = self._soft_cue(text)
        if cue is None:
            return []

        logger.warning(
            "SOFT_CUE_FALLBACK engaged: semantic layer %s (first=%s retried=%s), cue=%r; "
            "treating unknown risk as risk",
            intent.semantic_status.value,
            intent.semantic_first_parse_status.value,
            intent.semantic_retried,
            cue,
        )
        return [
            SafetyTrigger(
                layer=SafetyLayer.SOFT_CUE_FALLBACK,
                category=SafetyCategory.SEVERE_DISTRESS,
                detail=(
                    f"semantic classifier {intent.semantic_status.value} after retry "
                    f"(risk unknown, not low); crisis-adjacent cue present and no rule "
                    f"fired"
                ),
                matched_text=cue,
                score=0.0,
            )
        ]

    # ---------------------------------------------------------------- input
    def check_input(
        self,
        text: str,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        session_id: str | None = None,
    ) -> SafetyVerdict:
        """Gate the user's message. MUST be called before retrieval/generation."""
        triggers = self._run_rules(text, self.rules, SafetyLayer.RULE)
        rule_fired = bool(triggers)
        model_triggers, signals = self._run_risk_policy(emotion, intent)
        triggers.extend(model_triggers)
        triggers.extend(self._run_soft_cue_fallback(text, intent, rule_fired))

        verdict = SafetyVerdict(blocked=bool(triggers), triggers=triggers, signals=signals)
        if verdict.blocked:
            categories = [t.category for t in triggers]
            verdict.response = build_crisis_response(categories)

        self._log(text, verdict, emotion, intent, session_id)
        return verdict

    # --------------------------------------------------------------- output
    def check_output(
        self,
        answer: str,
        session_id: str | None = None,
        input_verdict: SafetyVerdict | None = None,
    ) -> SafetyVerdict:
        """Gate the generated answer (rule 4: after generation)."""
        triggers = self._run_rules(answer, self.output_rules, SafetyLayer.OUTPUT)
        verdict = SafetyVerdict(blocked=bool(triggers), triggers=triggers)
        if verdict.blocked:
            verdict.response = OUTPUT_FALLBACK_RESPONSE
            if input_verdict is not None and input_verdict.blocked:
                verdict.response = build_crisis_response(
                    [t.category for t in input_verdict.triggers]
                )
        self._log(answer, verdict, None, None, session_id, kind="output")
        return verdict

    # ----------------------------------------------------------------- misc
    def _log(
        self,
        text: str,
        verdict: SafetyVerdict,
        emotion: EmotionResult | None,
        intent: IntentResult | None,
        session_id: str | None,
        kind: str = "input",
    ) -> None:
        if not self.settings.safety_log_enabled or not verdict.requires_audit():
            return
        self.sink.record(
            SafetyEvent(
                session_id=session_id,
                message=f"[{kind}] {text[:2000]}",
                verdict=verdict,
                emotion_intensity=emotion.intensity if emotion else 0.0,
                dominant_emotion=emotion.dominant if emotion else "neutral",
                intent=intent.intent.value if intent else IntentLabel.OTHER.value,
            )
        )

    def crisis_response(self, verdict: SafetyVerdict) -> str:
        """Response text for a blocked verdict (falls back to the generic one)."""
        return verdict.response or build_crisis_response()
