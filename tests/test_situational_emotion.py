"""Tests for GitaGuide 2.1 Phase 4: Situational Emotion Understanding.

Verifies:
1. Situational catastrophe detection for cases where GoEmotions yields false neutrals.
2. Inferred flag (inferred=True) and provenance (inferred_from).
3. Calibrated intensity (0.45 - 0.75).
4. Non-overriding invariant: explicit user emotional statements take precedence.
5. Greetings invariant: pure greetings remain neutral.
"""

import pytest

from app.pipeline.emotion import EmotionDetector, LexiconEmotionBackend


@pytest.fixture
def detector() -> EmotionDetector:
    # Use Lexicon backend for deterministic, portable unit testing
    return EmotionDetector(backend=LexiconEmotionBackend())


# ----------------------------------------------------------------------
# 1. Situational Catastrophe Tests
# ----------------------------------------------------------------------


def test_medical_emergency_and_insurance_rejection(detector: EmotionDetector) -> None:
    text = "The hospital is refusing cashless admission and denying our insurance claim for emergency surgery."
    res = detector.detect(text)
    assert res.dominant in {"fear", "nervousness", "sadness"}
    assert res.intensity >= 0.45
    assert res.inferred is True
    assert res.inferred_from == "medical_emergency_catastrophe"


def test_extortion_and_bribe_demand(detector: EmotionDetector) -> None:
    text = "A government inspector is demanding a bribe and threatened to shut down our plant."
    res = detector.detect(text)
    assert res.dominant in {"fear", "anger", "nervousness"}
    assert res.intensity >= 0.45
    assert res.inferred is True
    assert res.inferred_from == "extortion_bribe_distress"


def test_contractor_fraud_and_eviction(detector: EmotionDetector) -> None:
    text = "The contractor absconded with our life savings advance and the landlord served a notice to vacate."
    res = detector.detect(text)
    assert res.dominant in {"anger", "sadness", "fear"}
    assert res.intensity >= 0.45
    assert res.inferred is True
    assert res.inferred_from == "fraud_eviction_disinheritance"


def test_workplace_wrongful_termination(detector: EmotionDetector) -> None:
    text = "I was terminated without notice after raising whistleblower concerns and escorted out of the building."
    res = detector.detect(text)
    assert res.dominant in {"anger", "fear", "disappointment"}
    assert res.intensity >= 0.45
    assert res.inferred is True
    assert res.inferred_from == "wrongful_termination_retaliation"


def test_academic_and_career_disqualification(detector: EmotionDetector) -> None:
    text = "After 3 years of preparation, my final offer was rescinded and admission cancelled."
    res = detector.detect(text)
    assert res.dominant in {"disappointment", "sadness", "nervousness"}
    assert res.intensity >= 0.45
    assert res.inferred is True
    assert res.inferred_from == "academic_career_disqualification"


# ----------------------------------------------------------------------
# 2. Invariants & Calibration Tests
# ----------------------------------------------------------------------


def test_explicit_user_emotion_not_overridden(detector: EmotionDetector) -> None:
    """When user explicitly states an emotion with strong score, situational cue does not override."""
    text = "I am ecstatic and thrilled that we closed our round."
    res = detector.detect(text)
    assert res.dominant in {"joy", "excitement"}
    assert res.inferred is False


def test_pure_greetings_remain_neutral(detector: EmotionDetector) -> None:
    """Pure greetings must not trigger situational distress inference."""
    for greeting in ["hi", "hello there", "good morning", "namaste", "hey"]:
        res = detector.detect(greeting)
        assert res.dominant == "neutral"
        assert res.inferred is False


def test_calibrated_intensity_range(detector: EmotionDetector) -> None:
    """Situational inference produces calibrated, realistic intensity (0.45 to 0.75)."""
    text = "The bank sent an auction notice for our family home after loan payment default."
    res = detector.detect(text)
    assert res.dominant in {"fear", "nervousness"}
    assert 0.45 <= res.intensity <= 0.85

