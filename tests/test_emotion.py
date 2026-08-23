"""Unit tests for stage 1 (emotion detection and intensity)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.pipeline.emotion import (
    AROUSAL_WEIGHTS,
    EmotionDetector,
    LexiconEmotionBackend,
    OnnxGoEmotionsBackend,
    _sigmoid,
    compute_intensity,
)
from app.pipeline.schemas import EmotionScore


def test_import_does_not_load_weights() -> None:
    """Rule 6: constructing the ONNX backend must not touch the network."""
    backend = OnnxGoEmotionsBackend(Settings(mock_mode=False))
    assert backend._session is None
    assert backend._tokenizer is None


def test_mock_mode_selects_lexicon_backend(settings: Settings) -> None:
    assert EmotionDetector(settings=settings).backend.name == "lexicon"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I am terrified of losing this job", "fear"),
        ("My father passed away last month", "grief"),
        ("I am furious that they took credit for my work", "anger"),
        ("I feel so anxious about the deadline", "nervousness"),
        ("I am confused about which offer to take", "confusion"),
        ("Thank you, this actually helped", "gratitude"),
    ],
)
def test_dominant_emotion(detector: EmotionDetector, text: str, expected: str) -> None:
    assert detector.detect(text).dominant == expected


def test_neutral_when_no_cue(detector: EmotionDetector) -> None:
    result = detector.detect("The meeting is scheduled for Thursday afternoon.")
    assert result.dominant == "neutral"
    assert result.intensity < 0.2


def test_labels_are_sorted_descending(detector: EmotionDetector) -> None:
    labels = detector.detect("I am anxious, sad and completely exhausted").labels
    scores = [item.score for item in labels]
    assert scores == sorted(scores, reverse=True)


def test_top_k_is_respected(settings: Settings) -> None:
    detector = EmotionDetector(
        settings=settings, backend=LexiconEmotionBackend(top_k=2)
    )
    result = detector.detect("I am anxious, sad, angry, guilty and confused")
    assert len(result.labels) <= 2


def test_batch_matches_single(detector: EmotionDetector) -> None:
    texts = ["I am scared", "I am angry"]
    batch = detector.detect_batch(texts)
    assert [item.dominant for item in batch] == [
        detector.detect(texts[0]).dominant,
        detector.detect(texts[1]).dominant,
    ]


# --------------------------------------------------------------------- intensity
def test_intensity_is_zero_without_labels() -> None:
    assert compute_intensity([]) == 0.0


def test_intensity_scales_with_arousal_weight() -> None:
    grief = compute_intensity([EmotionScore(emotion="grief", score=0.8)])
    joy = compute_intensity([EmotionScore(emotion="joy", score=0.8)])
    assert grief > joy
    assert AROUSAL_WEIGHTS["grief"] > AROUSAL_WEIGHTS["joy"]


def test_intensity_never_exceeds_one() -> None:
    labels = [EmotionScore(emotion=name, score=0.99) for name in ("grief", "fear", "sadness")]
    assert compute_intensity(labels, "everything is unbearable!!") <= 1.0


def test_lexical_intensifiers_raise_intensity() -> None:
    labels = [EmotionScore(emotion="sadness", score=0.6)]
    plain = compute_intensity(labels, "I feel low today")
    loaded = compute_intensity(labels, "I feel hopeless and I cannot take it anymore")
    assert loaded > plain


def test_ordinary_bereavement_stays_below_gate_threshold(
    detector: EmotionDetector, settings: Settings
) -> None:
    """Calibration guard: grief guidance is the core use case, not an emergency."""
    result = detector.detect("My father passed away last month and I cannot stop crying")
    assert result.dominant == "grief"
    assert result.intensity < settings.safety_intensity_threshold


def test_stacked_despair_crosses_gate_threshold(
    detector: EmotionDetector, settings: Settings
) -> None:
    result = detector.detect(
        "I am completely hopeless, nobody cares, and I cannot do this anymore"
    )
    assert result.intensity >= settings.safety_intensity_threshold


def test_single_score_of_helper(detector: EmotionDetector) -> None:
    result = detector.detect("I am terrified")
    assert result.score_of("fear") > 0
    assert result.score_of("gratitude") == 0.0


# ------------------------------------------------------------------ mechanics
@pytest.mark.parametrize(
    ("logit", "expected"), [(0.0, 0.5), (10.0, 0.9999), (-10.0, 0.0000454)]
)
def test_sigmoid(logit: float, expected: float) -> None:
    assert _sigmoid(logit) == pytest.approx(expected, abs=1e-4)


def test_detector_falls_back_when_backend_raises(settings: Settings) -> None:
    class Broken(LexiconEmotionBackend):
        name = "broken"

        def predict(self, texts):  # type: ignore[override]
            raise RuntimeError("weights missing")

    detector = EmotionDetector(settings=settings, backend=Broken())
    result = detector.detect("I am scared about tomorrow")
    assert result.backend == "lexicon"
    assert result.dominant == "fear"
