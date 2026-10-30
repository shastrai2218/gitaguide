"""Stage 2 - intent classification over six classes.

Called by: app.pipeline.orchestrator, stage 2.

Research-pipeline role
---------------------
Intent decides *how* the rest of the pipeline behaves:

* ``crisis``               -> the safety gate's model layer trips (no retrieval)
* ``professional_dilemma`` -> retrieval re-ranking favours the Arthashastra
* ``emotional_support``    -> re-ranking favours the Gita and emotion edges
* ``scripture_question``   -> exegesis framing, verse text weighted over advice
* ``seek_guidance``        -> the default balanced blend
* ``other``                -> small talk; short answer, minimal retrieval

The production classifier is zero-shot through the LLM wrapper, blended with a
transparent keyword prior so the system degrades gracefully (and works offline).
A fine-tuned classifier is future work - see :class:`FineTunedIntentClassifier`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.llm.base import TASK_INTENT, LLMError, LLMProvider, LLMResponse
from app.pipeline.schemas import (
    EmotionResult,
    IntentLabel,
    IntentResult,
    SemanticParseStatus,
    SemanticStatus,
)
from app.pipeline.textutils import (
    has_academic_framing,
    is_negated,
    is_third_party_past,
)

logger = logging.getLogger(__name__)

#: How much the LLM's zero-shot distribution counts against the keyword prior.
LLM_WEIGHT = 0.65
PRIOR_WEIGHT = 1.0 - LLM_WEIGHT

INTENT_DEFINITIONS: dict[IntentLabel, str] = {
    IntentLabel.SEEK_GUIDANCE: (
        "wants direction or a decision on a life situation that is not primarily work"
    ),
    IntentLabel.EMOTIONAL_SUPPORT: (
        "primarily expressing pain, loneliness, grief or anxiety and wanting to be heard"
    ),
    IntentLabel.PROFESSIONAL_DILEMMA: (
        "a work, career, business, money or leadership decision or conflict"
    ),
    IntentLabel.SCRIPTURE_QUESTION: (
        "asking what a text, verse, chapter or concept means, in an exegetical sense"
    ),
    IntentLabel.CRISIS: (
        "at risk of self-harm or suicide, being harmed by someone, or in a medical emergency"
    ),
    IntentLabel.OTHER: "greeting, small talk, meta questions about the system, or off-topic",
}

#: ``(regex, intent, weight)``. Kept flat and readable so it can be reported in
#: the dissertation as the interpretable baseline.
INTENT_CUES: tuple[tuple[str, IntentLabel, float], ...] = (
    # crisis - deliberately overweighted so it dominates after normalisation
    (r"\b(?:kill(?:ing)? myself|end my life|suicidal|suicide|want to die|self[- ]?harm|"
     r"cut(?:ting)? myself|better off dead|no reason to live|overdose|"
     r"threatens? to kill me|(?:beats?|hits?|hurts?) me)\b", IntentLabel.CRISIS, 2.6),
    # scripture question
    (r"\b(?:gita|bhagavad|arthashastra|kautilya|chanakya|krishna|arjuna|shloka|sloka|verse|"
     r"chapter \d+|sanskrit|dharma|karma yoga|moksha)\b", IntentLabel.SCRIPTURE_QUESTION, 0.55),
    (r"\b(?:what does .* (?:say|mean)|meaning of|explain|interpretation of|commentary on|"
     r"which verse|teach me about)\b", IntentLabel.SCRIPTURE_QUESTION, 0.5),
    # professional dilemma
    (r"\b(?:job|career|boss|manager|promotion|appraisal|resign|resignation|quit my job|"
     r"salary|raise|team|colleague|coworker|client|startup|business|company|office|"
     r"deadline|interview|offer letter|layoff|fired|workplace|employee|founder|"
     r"co[- ]?founder|investor|profit|loss|contract|negotiat)\w*\b",
     IntentLabel.PROFESSIONAL_DILEMMA, 0.6),
    (r"\b(?:hire|fire|delegate|micromanage|politics at work|credit for my work|"
     r"working (?:\w+|\d+) hours|workload|overtime|night shift|report(?:ing)? it)\b",
     IntentLabel.PROFESSIONAL_DILEMMA, 0.5),
    # emotional support
    (r"\b(?:sad|lonely|alone|depressed|anxious|anxiety|grief|grieving|crying|cry|empty|numb|"
     r"heartbroken|hopeless|worthless|exhausted|burnt ?out|overwhelmed|ashamed|guilty|"
     r"betrayed|hurt(?:ing)?|miss (?:him|her|them|my))\b", IntentLabel.EMOTIONAL_SUPPORT, 0.55),
    # "console" = consolation/comfort, never a programming console
    (r"\b(?:consol(?:e(?:d|s)?|ation|ing|ing me)?|comfort(?:ed|ing)?|solace|soothe|soothing|"
     r"reassur(?:e|ance|ing)|calm(?:ed|ing)? (?:me|my mind|down)|want to calm|need to calm)\b",
     IntentLabel.EMOTIONAL_SUPPORT, 0.7),
    # Frustration, disappointment, emotional distress
    (r"\b(?:frustrat(?:ed|ing|ion)|disappoint(?:ed|ing|ment)|angry|anger|upset|fed up|"
     r"annoyed|irritat(?:ed|ing)|stressed|stress(?:ful)?|disheartened|let down)\b",
     IntentLabel.EMOTIONAL_SUPPORT, 0.55),
    (r"\b(?:passed away|died|lost my (?:father|mother|mom|dad|wife|husband|son|daughter|"
     r"friend|brother|sister))\b", IntentLabel.EMOTIONAL_SUPPORT, 0.8),
    (r"\b(?:panic|panicking|cannot stop (?:thinking|crying)|can(?:no|')?t stop (?:thinking|crying)|"
     r"obsess\w*|hate myself|blame myself|forgive myself|not good enough)\b",
     IntentLabel.EMOTIONAL_SUPPORT, 0.6),
    # seek guidance
    (r"\b(?:what should i do|should i|how do i|how should i|help me (?:decide|choose)|"
     r"advice|advise|guide me|what would you do|i (?:am|'m) (?:torn|conflicted|stuck)|"
     r"can(?:no|')?t decide)\b", IntentLabel.SEEK_GUIDANCE, 0.6),
    (r"\b(?:marriage|family|parents|relationship|friendship|studies|exam|health|"
     r"purpose in life|meaning of my life)\b", IntentLabel.SEEK_GUIDANCE, 0.35),
    # other / small talk
    (r"^\s*(?:hi|hello|hey|namaste|good (?:morning|evening))\b", IntentLabel.OTHER, 0.9),
    (r"\b(?:who (?:are|made) you|what can you do|how do you work|thank you|thanks)\b",
     IntentLabel.OTHER, 0.7),
)

_COMPILED_CUES: tuple[tuple[re.Pattern[str], IntentLabel, float], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), intent, weight)
    for pattern, intent, weight in INTENT_CUES
)

#: Emotions that push a borderline turn toward ``emotional_support``.
SUPPORT_EMOTIONS = frozenset({"grief", "sadness", "remorse", "fear", "disappointment"})


def heuristic_intent_scores(text: str) -> dict[str, float]:
    """Interpretable keyword prior over the six intents (sums to 1.0).

    Also used by :class:`app.llm.fake.FakeLLMProvider` so that offline runs
    behave like a (weak) zero-shot classifier rather than a constant.
    """
    raw: dict[IntentLabel, float] = dict.fromkeys(IntentLabel, 0.0)
    suppress_crisis_cues = has_academic_framing(text) or is_third_party_past(text)
    for pattern, intent, weight in _COMPILED_CUES:
        matches = list(pattern.finditer(text))
        if intent is IntentLabel.CRISIS:
            # Identical negation/context rules to the safety gate (textutils), so
            # "I am not suicidal" does not route the turn to crisis.
            matches = (
                []
                if suppress_crisis_cues
                else [m for m in matches if not is_negated(text, m.start())]
            )
        if matches:
            raw[intent] += weight * (1.0 + 0.25 * (len(matches) - 1))

    if sum(raw.values()) == 0.0:
        raw[IntentLabel.OTHER] = 1.0
    else:
        # A small floor keeps every class non-zero for reporting.
        for label in raw:
            raw[label] += 0.02

    total = sum(raw.values())
    return {label.value: round(value / total, 4) for label, value in raw.items()}


def _blend(
    prior: Mapping[str, float],
    llm_scores: Mapping[str, float] | None,
) -> dict[str, float]:
    """Weighted blend of the two distributions, renormalised."""
    if not llm_scores:
        return dict(prior)
    blended = {
        label: PRIOR_WEIGHT * prior.get(label, 0.0) + LLM_WEIGHT * llm_scores.get(label, 0.0)
        for label in {*prior, *llm_scores}
        if label in {item.value for item in IntentLabel}
    }
    total = sum(blended.values()) or 1.0
    return {label: round(value / total, 4) for label, value in blended.items()}


@dataclass
class _SemanticAttempt:
    """One call to the semantic classifier and what came back.

    Defaults describe "never asked", so an offline classifier produces the same
    shape as a failed one without special-casing at the call site.
    """

    status: SemanticStatus = SemanticStatus.NOT_REQUESTED
    parse_status: SemanticParseStatus = SemanticParseStatus.NOT_ATTEMPTED
    provider: str = ""
    model: str = ""
    crisis_score: float = 0.0
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    scores: dict[str, float] | None = None
    rationale: str = "keyword prior only"
    backend: str = "prior"


def build_intent_prompt(text: str, emotion: EmotionResult | None = None) -> str:
    """Zero-shot prompt; the ``TASK:`` marker routes the fake provider."""
    taxonomy = "\n".join(
        f"- {label.value}: {description}" for label, description in INTENT_DEFINITIONS.items()
    )
    emotion_line = ""
    if emotion is not None and emotion.labels:
        tops = ", ".join(f"{item.emotion}={item.score:.2f}" for item in emotion.labels[:3])
        emotion_line = f"DETECTED EMOTION: {tops} (intensity {emotion.intensity:.2f})\n"

    return (
        f"TASK: {TASK_INTENT}\n"
        "Classify the user message into exactly one intent from this taxonomy:\n"
        f"{taxonomy}\n\n"
        "Rules:\n"
        "- If there is any indication of self-harm, suicide, being harmed by someone, or a "
        "medical emergency, the intent is crisis.\n"
        "- Passive suicidal ideation and perceived burdensomeness are crisis even without the "
        "words suicide, die, or self-harm.\n"
        "- Latin-script Hindi/Hinglish expressions of not wanting to live are crisis.\n"
        "- Explicit negation, recovered third-party history, figurative language, and academic "
        "discussion are not crisis unless the speaker also expresses current personal risk.\n"
        "- Work, money, career and leadership problems are professional_dilemma even when "
        "they are emotional.\n"
        "- Requests for comfort, consolation, soothing, calming down, or emotional solace (e.g., 'i need console', 'console me', 'how to calm my mind') are emotional_support or seek_guidance, never other or technical requests.\n"
        "- A statement explicitly bounded to leaving or being replaced on a team, project, or "
        "work role because of skills or fit is professional_dilemma, not perceived "
        "burdensomeness about the person's life. This exception is only for that explicit "
        "work scope; general claims that loved ones or people would be better off if the "
        "speaker were absent remain crisis.\n"
        "Examples:\n"
        '- "I think everyone would be better off without me" -> crisis\n'
        '- "My absence would make life easier for the people around me" -> crisis\n'
        '- "mujhe ab jeene ka mann nahi karta" -> crisis\n'
        '- "Mera jeene ka mann nahi hai" -> crisis\n'
        '- "I am not suicidal; I am asking about suffering" -> not crisis\n'
        '- "My friend was suicidal last year and recovered" -> not crisis\n'
        '- "This deadline is killing me" -> professional_dilemma, not crisis\n'
        '- "The team would be better off without me on this project because my skills do not '
        'match" -> professional_dilemma, not crisis\n'
        '- "The project needs someone more experienced than me in this role" -> '
        'professional_dilemma, not crisis\n'
        "Return ONLY one-line minified JSON: no markdown, explanation, or rationale. "
        "The scores array must contain all six probabilities in this exact order: "
        "seek_guidance, emotional_support, professional_dilemma, scripture_question, "
        "crisis, other. Example shape: "
        '{"intent":"crisis","confidence":0.95,"scores":[0,0,0,0,0.95,0.05]}\n\n'
        f"{emotion_line}"
        f"USER MESSAGE: {text}\n"
    )


class IntentClassifier:
    """Zero-shot LLM classifier blended with the keyword prior.

    Examples
    --------
    >>> import asyncio
    >>> from app.llm.fake import FakeLLMProvider
    >>> clf = IntentClassifier(llm=FakeLLMProvider())
    >>> asyncio.run(clf.classify("Should I quit my job for a startup?")).intent
    <IntentLabel.PROFESSIONAL_DILEMMA: 'professional_dilemma'>
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        settings: Settings | None = None,
        use_llm: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm
        self.use_llm = use_llm and llm is not None

    async def _attempt_semantic(
        self, text: str, emotion: EmotionResult | None
    ) -> _SemanticAttempt:
        """One call to the semantic classifier, parsed and classified.

        Returns an attempt record rather than raising, so the caller can decide
        whether an unusable result is worth a second try. Never raises.
        """
        try:
            response: LLMResponse = await self.llm.complete(  # type: ignore[union-attr]
                build_intent_prompt(text, emotion),
                max_tokens=self.settings.nvidia_intent_max_tokens,
                temperature=0.0,
            )
        except Exception:
            logger.exception("intent semantic classifier unavailable; using keyword prior")
            return _SemanticAttempt(
                status=SemanticStatus.UNAVAILABLE,
                parse_status=SemanticParseStatus.NOT_ATTEMPTED,
                provider=getattr(self.llm, "name", ""),
                model=getattr(self.llm, "model", ""),
                rationale="semantic classifier unavailable; keyword prior only",
                backend="prior(semantic-unavailable)",
            )

        attempt = _SemanticAttempt(
            status=SemanticStatus.MALFORMED,
            parse_status=SemanticParseStatus.INVALID_JSON,
            provider=response.provider,
            model=response.model,
            finish_reason=str(response.raw.get("finish_reason") or ""),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            backend=f"prior(semantic-malformed:{response.provider})",
            rationale="semantic classifier returned malformed JSON; keyword prior only",
        )

        try:
            payload = response.json()
        except LLMError:
            logger.warning(
                "intent semantic classifier returned UNPARSEABLE content (risk unknown, "
                "not low) provider=%s model=%s",
                response.provider,
                response.model,
            )
            return attempt

        normalised = self._normalise_llm_payload(payload) if isinstance(payload, Mapping) else {}
        if set(normalised) != {label.value for label in IntentLabel}:
            attempt.parse_status = SemanticParseStatus.INCOMPLETE_SCORES
            attempt.rationale = (
                "semantic classifier returned an incomplete intent distribution; "
                "keyword prior only"
            )
            logger.warning(
                "intent semantic classifier returned an INCOMPLETE distribution (risk "
                "unknown, not low) provider=%s model=%s keys=%s",
                response.provider,
                response.model,
                sorted(normalised),
            )
            return attempt

        attempt.status = SemanticStatus.OK
        attempt.parse_status = SemanticParseStatus.PARSED
        attempt.scores = normalised
        attempt.crisis_score = normalised.get(IntentLabel.CRISIS.value, 0.0)
        attempt.rationale = str(payload.get("rationale", "")) or "zero-shot classification"
        attempt.backend = f"llm:{response.provider}+prior"
        return attempt

    async def classify(
        self,
        text: str,
        emotion: EmotionResult | None = None,
    ) -> IntentResult:
        """Return the blended intent distribution for one message.

        A **malformed** semantic result gets exactly one immediate re-ask, same
        prompt and same token budget. Malformed output was measured at ~4.5% of
        live turns (docs/kg_measurements.md section 11) and on one of those turns
        the crisis gate missed a plain statement of suicidal ideation, because a
        failed parse left the gate with the rule layer alone. One retry takes the
        unprotected window to roughly the square of that rate.

        An **unavailable** classifier is not retried here: the provider already
        retries 429/5xx internally and has a fallback chain, so a second call
        would duplicate that and add latency to a path that is already failing.
        """
        prior = heuristic_intent_scores(text)
        attempt = _SemanticAttempt()
        first_parse_status = SemanticParseStatus.NOT_ATTEMPTED
        retried = False

        if self.use_llm and self.llm is not None:
            attempt = await self._attempt_semantic(text, emotion)
            first_parse_status = attempt.parse_status

            if attempt.status is SemanticStatus.MALFORMED:
                retried = True
                logger.warning(
                    "retrying malformed semantic intent call once (first=%s)",
                    first_parse_status.value,
                )
                retry = await self._attempt_semantic(text, emotion)
                retry.backend = f"{retry.backend}+retry"
                if retry.status is SemanticStatus.OK:
                    logger.info("semantic intent retry recovered a usable result")
                else:
                    logger.error(
                        "semantic intent retry ALSO failed (first=%s second=%s); the "
                        "semantic safety layer is unavailable for this turn",
                        first_parse_status.value,
                        retry.parse_status.value,
                    )
                attempt = retry

        scores = _blend(prior, attempt.scores)
        scores = self._apply_emotion_nudge(scores, emotion)
        best_label, best_score = max(scores.items(), key=lambda kv: kv[1])

        return IntentResult(
            intent=IntentLabel(best_label),
            confidence=round(best_score, 4),
            scores=scores,
            rationale=attempt.rationale,
            backend=attempt.backend,
            semantic_status=attempt.status,
            semantic_provider=attempt.provider,
            semantic_model=attempt.model,
            semantic_crisis_score=round(attempt.crisis_score, 4),
            semantic_parse_status=attempt.parse_status,
            semantic_first_parse_status=first_parse_status,
            semantic_retried=retried,
            semantic_finish_reason=attempt.finish_reason,
            semantic_prompt_tokens=attempt.prompt_tokens,
            semantic_completion_tokens=attempt.completion_tokens,
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _normalise_llm_payload(payload: Mapping[str, object]) -> dict[str, float]:
        """Normalise a full score mapping or the compact fixed-order score array."""
        valid = {item.value for item in IntentLabel}
        raw_scores = payload.get("scores")
        scores: dict[str, float] = {}
        if isinstance(raw_scores, Mapping):
            for key, value in raw_scores.items():
                if key in valid:
                    try:
                        scores[key] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        elif (
            isinstance(raw_scores, Sequence)
            and not isinstance(raw_scores, (str, bytes))
            and len(raw_scores) == len(IntentLabel)
        ):
            for label, value in zip(IntentLabel, raw_scores, strict=True):
                try:
                    scores[label.value] = max(0.0, min(1.0, float(value)))
                except (TypeError, ValueError):
                    return {}

        intent = payload.get("intent")
        if isinstance(intent, str) and intent in valid:
            try:
                confidence = float(payload.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            scores[intent] = max(scores.get(intent, 0.0), max(0.5, min(1.0, confidence)))

        total = sum(scores.values())
        if total <= 0:
            return {}
        return {key: value / total for key, value in scores.items()}

    @staticmethod
    def _apply_emotion_nudge(
        scores: Mapping[str, float],
        emotion: EmotionResult | None,
    ) -> dict[str, float]:
        """High-intensity negative affect makes ``emotional_support`` likelier.

        The nudge is capped and never touches ``crisis`` - crisis routing is the
        safety gate's job, not a soft score's.
        """
        out = dict(scores)
        if emotion is None or not emotion.labels:
            return out
        if emotion.dominant in SUPPORT_EMOTIONS and emotion.intensity >= 0.6:
            bonus = 0.12 * emotion.intensity
            out[IntentLabel.EMOTIONAL_SUPPORT.value] = min(
                1.0, out.get(IntentLabel.EMOTIONAL_SUPPORT.value, 0.0) + bonus
            )
            total = sum(out.values()) or 1.0
            out = {key: round(value / total, 4) for key, value in out.items()}
        return out


class FineTunedIntentClassifier(IntentClassifier):
    """FUTURE WORK - fine-tuned encoder classifier for the six intents.

    Planned: label ~2k GitaGuide turns, fine-tune ``distilroberta-base``, export
    to ONNX INT8 like the emotion model, and report macro-F1 against this
    zero-shot baseline in the evaluation chapter. Intentionally not implemented.
    """

    def __init__(self, model_path: str | None = None, **kwargs: object) -> None:
        raise NotImplementedError(
            "FineTunedIntentClassifier is future work; use IntentClassifier (zero-shot). "
            "Training data collection is tracked in the project plan."
        )

    async def classify(  # pragma: no cover - unreachable stub
        self,
        text: str,
        emotion: EmotionResult | None = None,
    ) -> IntentResult:
        raise NotImplementedError


def label_names() -> Sequence[str]:
    """All six intent labels, for the API schema and the frontend."""
    return [label.value for label in IntentLabel]
