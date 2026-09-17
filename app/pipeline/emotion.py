"""Stage 1 - emotion detection.

Called by: app.pipeline.orchestrator, stage 1.

Research-pipeline role
---------------------
Turns the raw user message into a multi-label emotion distribution plus a single
scalar ``intensity``.  Two things downstream depend on it:

1. the **safety audit** records ``intensity`` as non-blocking context, and
2. the **retriever** walks ``(:Emotion)-[:ADDRESSES_EMOTION]->(:Verse)`` edges
   using the top emotion labels, which is how affect actually steers retrieval.

Backends
--------
``OnnxGoEmotionsBackend``
    ``SamLowe/roberta-base-go_emotions`` exported to ONNX and INT8-quantised,
    executed on CPU through ``onnxruntime``.  Weights are downloaded lazily on
    the *first* prediction (or eagerly via :meth:`EmotionDetector.warmup`, which
    ``app.main``'s lifespan calls once at startup) so that importing this module
    never touches the network - tests stay offline.

``LexiconEmotionBackend``
    Deterministic keyword/regex backend used in mock mode and in unit tests.
    Same interface, no weights, no network.  It is intentionally conservative:
    it exists to keep the pipeline runnable, not to be a research baseline.
"""

from __future__ import annotations

import logging
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.pipeline.schemas import EmotionResult, EmotionScore

logger = logging.getLogger(__name__)

#: GoEmotions labels grouped by valence - used for intensity and by the gate.
NEGATIVE_EMOTIONS: frozenset[str] = frozenset(
    {
        "anger",
        "annoyance",
        "disappointment",
        "disapproval",
        "disgust",
        "embarrassment",
        "fear",
        "grief",
        "nervousness",
        "remorse",
        "sadness",
    }
)

CRISIS_RELEVANT_EMOTIONS: frozenset[str] = frozenset({"grief", "sadness", "fear", "remorse"})

#: Arousal weights: how much a label contributes to perceived intensity.
AROUSAL_WEIGHTS: dict[str, float] = {
    "grief": 1.00,
    "fear": 0.95,
    "anger": 0.90,
    "nervousness": 0.88,
    "sadness": 0.85,
    "remorse": 0.80,
    "disgust": 0.72,
    "disappointment": 0.70,
    "embarrassment": 0.65,
    "annoyance": 0.60,
    "disapproval": 0.58,
    "confusion": 0.55,
    "surprise": 0.50,
    "desire": 0.45,
    "excitement": 0.45,
    "joy": 0.35,
    "love": 0.30,
    "gratitude": 0.25,
    "relief": 0.20,
    "neutral": 0.05,
}
DEFAULT_AROUSAL = 0.45

#: Lexical markers that amplify intensity regardless of label distribution.
INTENSIFIER_PATTERNS: tuple[tuple[str, float], ...] = (
    (r"\bcan(?:no|')?t (?:take|bear|handle|do) (?:it|this) (?:any ?more|anymore)\b", 0.22),
    (r"\b(?:completely|totally|utterly|absolutely)\b", 0.06),
    (r"\b(?:unbearable|unbearably|excruciating|devastated|shattered|broken)\b", 0.18),
    (r"\b(?:hopeless|worthless|pointless|meaningless|empty inside)\b", 0.20),
    (r"\bevery ?(?:single )?(?:day|night)\b", 0.06),
    (r"\bnobody (?:cares|understands|listens)\b", 0.12),
    (r"!{2,}", 0.05),
)

_TOKEN_RE = re.compile(r"[a-z']+")


class EmotionBackend(ABC):
    """Common interface so the detector is indifferent to how scores appear."""

    name: str = "abstract"

    @abstractmethod
    def predict(self, texts: Sequence[str]) -> list[list[EmotionScore]]:
        """Return per-text label distributions, sorted descending by score."""

    def warmup(self) -> None:  # pragma: no cover - trivial for fakes
        """Force any lazy resource (weights, sessions) to materialise."""
        self.predict(["warmup"])


class LexiconEmotionBackend(EmotionBackend):
    """Offline, deterministic emotion scorer (mock mode / unit tests)."""

    name = "lexicon"

    #: ``emotion -> ((regex, weight), ...)``.  Weights are calibrated so a
    #: single explicit cue lands mid-range and stacked cues saturate.
    LEXICON: dict[str, tuple[tuple[str, float], ...]] = {
        "grief": (
            (r"\b(?:passed away|died|death of|funeral|lost (?:my|our) (?:father|mother|mom|dad|son|daughter|wife|husband|brother|sister|friend|child))\b", 0.90),
            (r"\b(?:grief|grieving|mourning|bereaved)\b", 0.85),
            (r"\b(?:want to die|end my life|kill myself|no reason to live)\b", 0.88),
        ),
        "sadness": (
            (r"\b(?:sad|unhappy|down|low|miserable|heartbroken|depressed|crying|cry|tears)\b", 0.62),
            (r"\b(?:lonely|alone|empty|numb)\b", 0.58),
            (r"\b(?:hopeless|worthless|pointless|no reason to live|want to die|end my life|kill myself)\b", 0.92),
        ),
        "fear": (
            (r"\b(?:afraid|scared|terrified|frightened|panic|panicking|dread)\b", 0.78),
            (r"\b(?:what if|worried|worry|worrying)\b", 0.52),
            (r"\b(?:he|she|they) (?:will |might |may )?(?:hurt|beat|kill|attack) (?:me|us)\b", 0.90),
        ),
        "nervousness": (
            (r"\b(?:anxious|anxiety|nervous|stress\w*|tense|restless|overwhelmed|burnt ?out|burn ?out)\b", 0.66),
            (r"\b(?:deadline|interview|exam|appraisal|presentation)\b", 0.42),
            (r"\b(?:insomnia|can(?:no|')?t sleep|sleepless)\b", 0.55),
        ),
        "anger": (
            (r"\b(?:angry|furious|rage|enraged|livid|hate|resent|resentful)\b", 0.76),
            (r"\b(?:unfair|betrayed|cheated|humiliated|insulted)\b", 0.60),
        ),
        "annoyance": (
            (r"\b(?:annoyed|irritated|frustrated|frustrating|fed up)\b", 0.60),
        ),
        "disappointment": (
            (r"\b(?:disappointed|let down|failed|failure|rejected|rejection|passed over)\b", 0.62),
            (r"\b(?:not good enough|behind everyone)\b", 0.58),
        ),
        "remorse": (
            (r"\b(?:guilty|guilt|regret|regretful|my fault|ashamed|shame|sorry for what i)\b", 0.66),
        ),
        "disgust": (
            (r"\b(?:disgusted|sickened|repulsed|corrupt|corruption|bribe|bribery)\b", 0.55),
        ),
        "embarrassment": (
            (r"\b(?:embarrassed|humiliated|awkward|made a fool)\b", 0.56),
        ),
        "confusion": (
            (r"\b(?:confused|conflicted|torn|unsure|do(?:n't| not) know what to do|dilemma|two minds)\b", 0.64),
            (r"\b(?:should i|which (?:one|path|option))\b", 0.46),
        ),
        "desire": (
            (r"\b(?:want|wish|crave|long for|ambition|promotion)\b", 0.40),
        ),
        "curiosity": (
            (r"\b(?:what does|explain|meaning of|teach me|tell me about)\b", 0.50),
            (r"\?\s*$", 0.30),
        ),
        "gratitude": (
            (r"\b(?:thank you|thanks|grateful|gratitude)\b", 0.70),
        ),
        "joy": (
            (r"\b(?:happy|glad|delighted|excited|great news|joy)\b", 0.66),
        ),
        "relief": (
            (r"\b(?:relieved|relief|finally over|at peace)\b", 0.62),
        ),
        "love": (
            (r"\b(?:love|loving|affection|care deeply)\b", 0.58),
        ),
        "approval": (
            (r"\b(?:agree|makes sense|you(?:'re| are) right)\b", 0.50),
        ),
    }

    def __init__(self, top_k: int = 5, threshold: float = 0.05) -> None:
        self.top_k = top_k
        self.threshold = threshold
        self._compiled: dict[str, tuple[tuple[re.Pattern[str], float], ...]] = {
            emotion: tuple((re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in cues)
            for emotion, cues in self.LEXICON.items()
        }

    def predict(self, texts: Sequence[str]) -> list[list[EmotionScore]]:
        return [self._score_one(text) for text in texts]

    def _score_one(self, text: str) -> list[EmotionScore]:
        scores: dict[str, float] = {}
        for emotion, cues in self._compiled.items():
            hits = [weight for pattern, weight in cues if pattern.search(text)]
            if not hits:
                continue
            # Strongest cue sets the level; extra cues nudge it up.
            score = max(hits) + 0.06 * (len(hits) - 1)
            scores[emotion] = min(0.97, round(score, 4))

        if not scores:
            return [EmotionScore(emotion="neutral", score=0.6)]

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            EmotionScore(emotion=emotion, score=score)
            for emotion, score in ranked[: self.top_k]
            if score >= self.threshold
        ]

    def warmup(self) -> None:
        return None


class OnnxGoEmotionsBackend(EmotionBackend):
    """``SamLowe/roberta-base-go_emotions`` as INT8 ONNX on CPU.

    The heavy imports (``transformers``, ``onnxruntime``, ``huggingface_hub``)
    happen inside :meth:`_ensure_loaded`, never at module import, which keeps
    ``pytest`` runnable on a machine with no model cache (rule 6).
    """

    name = "onnx-int8-go_emotions"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._session: object | None = None
        self._tokenizer: object | None = None
        self._id2label: dict[int, str] = {}

    # ------------------------------------------------------------- loading
    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import AutoConfig, AutoTokenizer

        settings = self.settings
        logger.info("loading emotion model %s (onnx int8)", settings.emotion_onnx_repo)

        model_path = hf_hub_download(
            repo_id=settings.emotion_onnx_repo,
            filename=settings.emotion_onnx_file,
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.onnx_intra_op_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            model_path,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(settings.emotion_model_id)
        config = AutoConfig.from_pretrained(settings.emotion_model_id)
        self._id2label = {int(k): v for k, v in config.id2label.items()}

    def warmup(self) -> None:
        self._ensure_loaded()
        self.predict(["I am not sure what to do next."])

    # ---------------------------------------------------------- inference
    def predict(self, texts: Sequence[str]) -> list[list[EmotionScore]]:
        self._ensure_loaded()
        assert self._session is not None and self._tokenizer is not None  # for type checkers

        encoded = self._tokenizer(  # type: ignore[operator]
            list(texts),
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="np",
        )
        input_names = {item.name for item in self._session.get_inputs()}  # type: ignore[attr-defined]
        feed = {name: value for name, value in encoded.items() if name in input_names}
        logits = self._session.run(None, feed)[0]  # type: ignore[attr-defined]

        out: list[list[EmotionScore]] = []
        for row in logits:
            scored = [
                EmotionScore(
                    emotion=self._id2label.get(index, f"label_{index}"),
                    score=round(_sigmoid(float(value)), 4),
                )
                for index, value in enumerate(row)
            ]
            scored.sort(key=lambda item: -item.score)
            kept = [
                item
                for item in scored[: self.settings.emotion_top_k]
                if item.score >= self.settings.emotion_score_threshold
            ]
            out.append(kept or scored[:1])
        return out


def _sigmoid(value: float) -> float:
    """Numerically stable logistic function (GoEmotions is multi-label)."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def compute_intensity(labels: Sequence[EmotionScore], text: str = "") -> float:
    """Collapse a label distribution into one arousal-weighted intensity.

    The formula is deliberately transparent (it appears in the dissertation and
    the safety gate depends on it):

    ``intensity = 0.75 * primary + 0.15 * support + lexical``

    * ``primary``  - top label score scaled by its arousal weight,
    * ``support``  - remaining labels' weighted mass (co-activation),
    * ``lexical``  - additive bonus for explicit intensifier phrases.

    Calibration note
    ----------------
    The coefficients were calibrated against ``safety_intensity_threshold``
    (0.85) so ordinary bereavement remains distinguishable from stacked despair.
    Intensity is now audit context only: crossing that calibration marker never
    blocks without an independent deterministic or semantic crisis signal.
    """
    if not labels:
        return 0.0

    def weight(emotion: str) -> float:
        return AROUSAL_WEIGHTS.get(emotion, DEFAULT_AROUSAL)

    primary = 0.75 * labels[0].score * weight(labels[0].emotion)
    support = 0.15 * sum(item.score * weight(item.emotion) for item in labels[1:])
    lexical = 0.0
    if text:
        lowered = text.lower()
        lexical = sum(
            bonus for pattern, bonus in INTENSIFIER_PATTERNS if re.search(pattern, lowered)
        )
        if text.isupper() and len(text) > 12:
            lexical += 0.05

    return round(min(1.0, max(0.0, primary + support + lexical)), 4)


class EmotionDetector:
    """Stage-1 detector; picks a backend from settings and stays lazy.

    Examples
    --------
    >>> detector = EmotionDetector(backend=LexiconEmotionBackend())
    >>> result = detector.detect("I am terrified of losing this job")
    >>> result.dominant
    'fear'
    """

    def __init__(
        self,
        settings: Settings | None = None,
        backend: EmotionBackend | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._backend = backend
        self._backend_failed = False

    # ------------------------------------------------------------ backend
    @property
    def backend(self) -> EmotionBackend:
        if self._backend is None:
            if self.settings.emotion_model_enabled and not self._backend_failed:
                self._backend = OnnxGoEmotionsBackend(self.settings)
            else:
                self._backend = LexiconEmotionBackend(
                    top_k=self.settings.emotion_top_k,
                    threshold=self.settings.emotion_score_threshold,
                )
        return self._backend

    def warmup(self) -> None:
        """Load weights once at application startup (called from lifespan)."""
        try:
            self.backend.warmup()
        except Exception:  # pragma: no cover - depends on network/cache
            logger.exception("emotion backend warmup failed; falling back to lexicon")
            self._fallback()

    def _fallback(self) -> None:
        self._backend_failed = True
        self._backend = LexiconEmotionBackend(
            top_k=self.settings.emotion_top_k,
            threshold=self.settings.emotion_score_threshold,
        )

    # ---------------------------------------------------------- inference
    def detect(self, text: str) -> EmotionResult:
        """Score a single message."""
        return self.detect_batch([text])[0]

    def detect_batch(self, texts: Sequence[str]) -> list[EmotionResult]:
        """Score several messages; falls back to the lexicon on backend error."""
        if not texts:
            return []
        started = time.perf_counter()
        backend = self.backend
        try:
            batches = backend.predict(texts)
        except Exception:  # pragma: no cover - network/runtime failures
            logger.exception("emotion backend %s failed; using lexicon", backend.name)
            self._fallback()
            backend = self.backend
            batches = backend.predict(texts)

        elapsed = (time.perf_counter() - started) * 1000.0
        return [
            EmotionResult(
                labels=list(labels),
                intensity=compute_intensity(labels, text),
                backend=backend.name,
                latency_ms=round(elapsed / len(texts), 3),
            )
            for text, labels in zip(texts, batches, strict=True)
        ]

    async def adetect(self, text: str) -> EmotionResult:
        """Async wrapper (inference is CPU-bound but sub-100ms on INT8)."""
        return self.detect(text)
