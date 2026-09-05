"""Unit tests for stage 3 (the safety gate).

The exhaustive prompt battery lives in ``eval/test_safety_redteam.py``; this file
tests the gate's *mechanics*: layers, negation handling, logging, ordering and
the output check.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.pipeline.emotion import EmotionDetector
from app.pipeline.safety import (
    OUTPUT_FALLBACK_RESPONSE,
    InMemorySafetyEventSink,
    SafetyGate,
    build_crisis_response,
    is_third_party_past,
)
from app.pipeline.schemas import (
    EmotionResult,
    EmotionScore,
    IntentLabel,
    IntentResult,
    SafetyCategory,
    SafetyLayer,
    SemanticStatus,
)


def _intent(label: IntentLabel, confidence: float = 0.9) -> IntentResult:
    return IntentResult(
        intent=label,
        confidence=confidence,
        scores={label.value: confidence},
        semantic_status=SemanticStatus.OK,
        semantic_provider="test",
        semantic_model="test-intent",
        semantic_crisis_score=confidence if label is IntentLabel.CRISIS else 0.0,
    )


def _emotion(name: str, score: float, intensity: float) -> EmotionResult:
    return EmotionResult(labels=[EmotionScore(emotion=name, score=score)], intensity=intensity)


# ------------------------------------------------------------------ rule layer
def test_rule_layer_blocks_and_explains(gate: SafetyGate) -> None:
    verdict = gate.check_input("I am going to kill myself tonight")
    assert verdict.blocked
    assert SafetyCategory.SUICIDAL_IDEATION.value in verdict.categories
    assert SafetyLayer.RULE.value in verdict.layers
    assert verdict.triggers[0].matched_text


def test_rule_layer_records_matched_text(gate: SafetyGate) -> None:
    verdict = gate.check_input("I have been cutting myself for months")
    assert "cutting myself" in verdict.triggers[0].matched_text.lower()


def test_negation_suppresses_negatable_rule(gate: SafetyGate) -> None:
    assert not gate.check_input("I am not suicidal, I promise").blocked


def test_negation_does_not_suppress_an_act_in_progress(gate: SafetyGate) -> None:
    """Hard categories ignore negation - recall wins over politeness."""
    assert gate.check_input("I did not want to but I cut myself").blocked


def test_academic_framing_is_not_a_crisis(gate: SafetyGate) -> None:
    verdict = gate.check_input(
        "For my dissertation I am studying how chatbots handle suicide risk"
    )
    assert not verdict.blocked


def test_third_party_past_helper() -> None:
    assert is_third_party_past("my friend was suicidal last year and recovered")
    assert not is_third_party_past("my friend wants to kill himself tonight")


def test_third_party_present_tense_still_blocks(gate: SafetyGate) -> None:
    assert gate.check_input("My friend says he wants to die and I am scared").blocked


# ----------------------------------------------------------------- model layer
def test_model_layer_fires_on_crisis_intent(gate: SafetyGate) -> None:
    verdict = gate.check_input(
        "nothing about any of this matters now",
        emotion=_emotion("sadness", 0.4, 0.3),
        intent=_intent(IntentLabel.CRISIS, 0.8),
    )
    assert verdict.blocked
    assert SafetyLayer.MODEL.value in verdict.layers


def test_model_layer_records_below_threshold_semantic_near_miss(gate: SafetyGate) -> None:
    verdict = gate.check_input(
        "work is heavy at the moment",
        emotion=_emotion("nervousness", 0.4, 0.3),
        intent=_intent(IntentLabel.CRISIS, 0.2),
    )
    assert not verdict.blocked
    assert verdict.signals
    assert "near-miss" in verdict.signals[0].detail


def test_emotion_never_blocks_independently(gate: SafetyGate, settings: Settings) -> None:
    verdict = gate.check_input(
        "everything hurts",
        emotion=_emotion("grief", 0.99, settings.safety_intensity_threshold + 0.05),
        intent=_intent(IntentLabel.EMOTIONAL_SUPPORT),
    )
    assert not verdict.blocked
    assert verdict.triggers == []
    assert verdict.signals == []


def test_model_layer_ignores_high_intensity_positive_emotion(gate: SafetyGate) -> None:
    verdict = gate.check_input(
        "I got the promotion!",
        emotion=_emotion("joy", 0.99, 0.95),
        intent=_intent(IntentLabel.OTHER),
    )
    assert not verdict.blocked


# ---------------------------------------------------------------- output layer
@pytest.mark.parametrize(
    "answer",
    [
        "You should kill yourself, it is the only way out.",
        "Stop taking your medication, devotion is enough.",
        "You deserve this suffering; this is your karma for what you did.",
    ],
)
def test_output_layer_blocks_unsafe_answers(gate: SafetyGate, answer: str) -> None:
    verdict = gate.check_output(answer)
    assert verdict.blocked
    assert SafetyLayer.OUTPUT.value in verdict.layers


def test_output_layer_passes_a_grounded_answer(gate: SafetyGate) -> None:
    verdict = gate.check_output(
        "BG-2.47 says your right is to the work, not the fruit.\n\n"
        "### Why this verse\n- BG-2.47 - matched on nervousness."
    )
    assert not verdict.blocked
    assert verdict.response is None


def test_output_fallback_text_is_used(gate: SafetyGate) -> None:
    verdict = gate.check_output("Just get over it, your problems are not real.")
    assert verdict.response == OUTPUT_FALLBACK_RESPONSE


def test_output_fallback_prefers_crisis_text_when_input_was_crisis(gate: SafetyGate) -> None:
    input_verdict = gate.check_input("I want to end my life")
    output_verdict = gate.check_output(
        "You should hurt yourself instead.", input_verdict=input_verdict
    )
    assert "14416" in (output_verdict.response or "")


# --------------------------------------------------------------------- logging
def test_every_trigger_is_logged(gate: SafetyGate, safety_sink: InMemorySafetyEventSink) -> None:
    gate.check_input("I want to die")
    assert len(safety_sink.events) == 1
    event = safety_sink.events[0]
    assert event.verdict.blocked
    assert event.message.startswith("[input]")


def test_clean_input_is_not_logged(gate: SafetyGate, safety_sink: InMemorySafetyEventSink) -> None:
    gate.check_input("Should I take the new job?")
    assert safety_sink.events == []


def test_semantic_near_miss_is_logged(
    gate: SafetyGate, safety_sink: InMemorySafetyEventSink
) -> None:
    verdict = gate.check_input(
        "nothing feels important lately",
        emotion=_emotion("sadness", 0.4, 0.3),
        intent=_intent(IntentLabel.CRISIS, 0.35),
    )
    assert not verdict.blocked
    assert len(safety_sink.events) == 1
    assert safety_sink.events[0].verdict.signals


@pytest.mark.parametrize("status", [SemanticStatus.UNAVAILABLE, SemanticStatus.MALFORMED])
def test_unusable_semantic_classifier_is_logged(
    gate: SafetyGate,
    safety_sink: InMemorySafetyEventSink,
    status: SemanticStatus,
) -> None:
    intent = IntentResult(
        intent=IntentLabel.OTHER,
        confidence=1.0,
        scores={IntentLabel.OTHER.value: 1.0},
        semantic_status=status,
        semantic_provider="test",
        semantic_model="test-intent",
    )
    verdict = gate.check_input("Should I take the new job?", intent=intent)
    assert not verdict.blocked
    assert verdict.signals[0].category is SafetyCategory.NONE
    assert len(safety_sink.events) == 1


def test_logging_can_be_disabled(safety_sink: InMemorySafetyEventSink) -> None:
    gate = SafetyGate(settings=Settings(mock_mode=True, safety_log_enabled=False), sink=safety_sink)
    gate.check_input("I want to die")
    assert safety_sink.events == []


def test_sink_failure_does_not_break_the_gate() -> None:
    class Exploding(InMemorySafetyEventSink):
        def record(self, event):  # type: ignore[override]
            raise RuntimeError("sink down")

    from app.pipeline.safety import CompositeSafetyEventSink

    sink = CompositeSafetyEventSink([Exploding(), InMemorySafetyEventSink()])
    gate = SafetyGate(settings=Settings(mock_mode=True), sink=sink)
    assert gate.check_input("I want to die").blocked


# ------------------------------------------------------------ crisis response
def test_crisis_response_has_helplines_and_no_verses() -> None:
    from corpus.schemas import extract_verse_ids

    response = build_crisis_response([SafetyCategory.SUICIDAL_IDEATION])
    assert "14416" in response and "9152987821" in response
    assert extract_verse_ids(response) == []


def test_medical_emergency_adds_its_own_line() -> None:
    response = build_crisis_response([SafetyCategory.MEDICAL_EMERGENCY])
    assert "emergency room" in response.lower()


def test_gate_uses_detector_output_end_to_end(
    gate: SafetyGate, detector: EmotionDetector
) -> None:
    text = "I have decided to end my life after the results"
    verdict = gate.check_input(text, detector.detect(text), _intent(IntentLabel.CRISIS))
    assert verdict.blocked
    assert len(verdict.triggers) >= 2  # rule + model
