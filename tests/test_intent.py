"""Intent-classifier tests for semantic safety provenance and parse honesty."""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings
from app.llm.base import LLMError, LLMProvider, LLMResponse
from app.llm.fake import FakeLLMProvider
from app.pipeline.intent import IntentClassifier, build_intent_prompt
from app.pipeline.schemas import IntentLabel, SemanticParseStatus, SemanticStatus


class UnavailableLLM(LLMProvider):
    """Provider double that represents a failed semantic-classifier call."""

    name = "unavailable-test"
    model = "offline"

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        raise LLMError("semantic endpoint unavailable")


class ResponseLLM(LLMProvider):
    """Provider double for preserving completion diagnostics through parsing."""

    name = "nvidia:test"
    model = "test-model"

    def __init__(self, response: LLMResponse) -> None:
        self.response = response

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        return self.response


def test_intent_prompt_covers_indirect_multilingual_and_benign_contexts() -> None:
    prompt = build_intent_prompt("example")
    assert "everyone would be better off without me" in prompt
    assert "mujhe ab jeene ka mann nahi karta" in prompt
    assert "I am not suicidal" in prompt
    assert "deadline is killing me" in prompt
    assert "on this project because my skills do not match" in prompt
    assert "My absence would make life easier" in prompt
    assert all(label.value in prompt for label in IntentLabel)


async def test_valid_semantic_result_records_provider_model_and_crisis_score() -> None:
    response = (
        '{"intent":"crisis","confidence":0.8,"scores":{"seek_guidance":0.02,'
        '"emotional_support":0.08,"professional_dilemma":0.02,'
        '"scripture_question":0.01,"crisis":0.8,"other":0.07}}'
    )
    classifier = IntentClassifier(
        llm=FakeLLMProvider(scripted=[response]), settings=Settings(mock_mode=True)
    )
    result = await classifier.classify("nothing about any of this matters")
    assert result.semantic_status is SemanticStatus.OK
    assert result.semantic_provider == "fake"
    assert result.semantic_model == "fake-deterministic-v1"
    assert result.semantic_crisis_score > 0.5
    assert result.semantic_parse_status is SemanticParseStatus.PARSED


async def test_compact_score_array_is_accepted() -> None:
    response = (
        '{"intent":"professional_dilemma","confidence":0.96,'
        '"scores":[0.01,0.01,0.96,0.0,0.0,0.02]}'
    )
    classifier = IntentClassifier(
        llm=FakeLLMProvider(scripted=[response]), settings=Settings(mock_mode=True)
    )
    result = await classifier.classify(
        "The team would be better off without me on this project because my skills do not match."
    )
    assert result.semantic_status is SemanticStatus.OK
    assert result.semantic_parse_status is SemanticParseStatus.PARSED
    assert result.semantic_crisis_score == 0.0
    assert result.intent is IntentLabel.PROFESSIONAL_DILEMMA


async def test_malformed_semantic_result_is_reported_honestly() -> None:
    """Both attempts unparseable: still malformed, and it says both failed."""
    llm = FakeLLMProvider(scripted=["not json", "still not json"])
    classifier = IntentClassifier(llm=llm, settings=Settings(mock_mode=True))
    result = await classifier.classify("Should I take the new job?")

    assert result.semantic_status is SemanticStatus.MALFORMED
    assert result.semantic_provider == "fake"
    assert result.semantic_model == "fake-deterministic-v1"
    assert result.backend.startswith("prior(semantic-malformed")
    assert result.semantic_parse_status is SemanticParseStatus.INVALID_JSON
    assert result.semantic_first_parse_status is SemanticParseStatus.INVALID_JSON
    assert result.semantic_retried is True
    assert len(llm.calls) == 2, "a malformed result must be re-asked exactly once"


async def test_incomplete_semantic_distribution_is_malformed() -> None:
    response = '{"intent":"other","confidence":0.9,"scores":{"other":0.9}}'
    llm = FakeLLMProvider(scripted=[response, response])
    classifier = IntentClassifier(llm=llm, settings=Settings(mock_mode=True))
    result = await classifier.classify("Should I take the new job?")

    assert result.semantic_status is SemanticStatus.MALFORMED
    assert result.semantic_crisis_score == 0.0
    assert result.semantic_parse_status is SemanticParseStatus.INCOMPLETE_SCORES
    assert result.semantic_retried is True


async def test_a_malformed_first_attempt_is_retried_and_can_recover() -> None:
    """The point of the retry: one bad parse must not disarm the layer.

    ~4.5% of live turns returned unparseable content, and one of those was a
    crisis miss. The retry has to actually replace the unusable result with the
    usable one, while still recording that the first attempt failed.
    """
    good = (
        '{"intent":"crisis","confidence":0.95,"rationale":"explicit",'
        '"scores":{"seek_guidance":0.01,"emotional_support":0.02,'
        '"professional_dilemma":0.0,"scripture_question":0.0,'
        '"crisis":0.95,"other":0.02}}'
    )
    llm = FakeLLMProvider(scripted=["not json", good])
    classifier = IntentClassifier(llm=llm, settings=Settings(mock_mode=True))
    result = await classifier.classify("I do not want to be alive anymore")

    assert result.semantic_status is SemanticStatus.OK
    assert result.semantic_parse_status is SemanticParseStatus.PARSED
    assert result.semantic_crisis_score == 0.95
    # The evidence that the model misbehaves must survive the recovery.
    assert result.semantic_first_parse_status is SemanticParseStatus.INVALID_JSON
    assert result.semantic_retried is True
    assert result.backend.endswith("+retry")


async def test_a_usable_first_attempt_is_not_retried() -> None:
    """One extra call on a 4.5% path is cheap; on every path it is not."""
    good = (
        '{"intent":"professional_dilemma","confidence":0.8,"rationale":"work",'
        '"scores":{"seek_guidance":0.1,"emotional_support":0.05,'
        '"professional_dilemma":0.8,"scripture_question":0.0,'
        '"crisis":0.0,"other":0.05}}'
    )
    llm = FakeLLMProvider(scripted=[good])
    classifier = IntentClassifier(llm=llm, settings=Settings(mock_mode=True))
    result = await classifier.classify("Should I take the new job?")

    assert result.semantic_status is SemanticStatus.OK
    assert result.semantic_retried is False
    assert len(llm.calls) == 1


async def test_truncated_semantic_json_is_unavailable_for_risk_scoring() -> None:
    response = LLMResponse(
        text='{"intent":"crisis","confidence":0.95,"scores":[0,0,0,0,0.95',
        provider="nvidia:test",
        model="test-model",
        prompt_tokens=501,
        completion_tokens=128,
        raw={"finish_reason": "length"},
    )
    classifier = IntentClassifier(
        llm=ResponseLLM(response), settings=Settings(mock_mode=True)
    )
    result = await classifier.classify("My absence would make life easier for everyone")
    assert result.semantic_status is SemanticStatus.MALFORMED
    assert result.semantic_parse_status is SemanticParseStatus.INVALID_JSON
    assert result.semantic_finish_reason == "length"
    assert result.semantic_prompt_tokens == 501
    assert result.semantic_completion_tokens == 128
    assert result.semantic_crisis_score == 0.0


async def test_unavailable_semantic_result_is_reported_honestly() -> None:
    classifier = IntentClassifier(llm=UnavailableLLM(), settings=Settings(mock_mode=True))
    result = await classifier.classify("Should I take the new job?")
    assert result.semantic_status is SemanticStatus.UNAVAILABLE
    assert result.semantic_provider == "unavailable-test"
    assert result.semantic_model == "offline"
    assert result.backend == "prior(semantic-unavailable)"
    assert result.semantic_parse_status is SemanticParseStatus.NOT_ATTEMPTED
