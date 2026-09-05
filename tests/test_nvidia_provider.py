"""Unit tests for the NVIDIA NIM provider.

**No test here touches the network.** A stub async client is injected, so neither
the ``openai`` SDK nor an API key is required, and ``conftest`` overrides
``NVIDIA_API_KEY`` with a dummy so the real key in ``.env`` can never be used.
Live verification lives in ``scripts/smoke_nvidia.py``, which is run by hand.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import LLMProviderName, Settings
from app.llm.base import TASK_ANSWER, TASK_INTENT, FallbackLLMProvider, LLMError
from app.llm.factory import build_llm_provider, build_nvidia_chain
from app.llm.fake import FakeLLMProvider
from app.llm.nvidia_provider import (
    NvidiaProvider,
    backoff_delay,
    error_status,
    is_intent_prompt,
    is_retryable,
    retry_after_seconds,
    strip_think_blocks,
)

ANSWER_PROMPT = f"TASK: {TASK_ANSWER}\n\nCONTEXT:\n[BG-2.47] ...\nUSER MESSAGE: I am anxious"
INTENT_PROMPT = f"TASK: {TASK_INTENT}\n\nUSER MESSAGE: should I resign"


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class StubError(Exception):
    """Stands in for ``openai.APIStatusError`` (duck-typed by the provider)."""

    def __init__(self, status_code: int, message: str = "", retry_after: str | None = None):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        self.headers = {"retry-after": retry_after} if retry_after is not None else {}


class StubCompletion:
    """Minimal shape of an OpenAI chat completion object."""

    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        message = type("Message", (), {"content": content, "role": "assistant"})()
        choice = type("Choice", (), {"message": message, "finish_reason": finish_reason})()
        self.choices = [choice]
        self.usage = type("Usage", (), {"prompt_tokens": 120, "completion_tokens": 60})()


class StubClient:
    """Async client that replays a queue of completions/exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        outer = self

        class Completions:
            async def create(self, **kwargs: Any) -> Any:
                outer.calls.append(kwargs)
                if not outer.script:
                    raise AssertionError("StubClient ran out of scripted responses")
                item = outer.script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item

        self.chat = type("Chat", (), {"completions": Completions()})()

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def nvidia_settings() -> Settings:
    """Fast retries so the tests do not sleep for real (delays are patched too)."""
    return Settings(
        mock_mode=False,
        nvidia_api_key="nvapi-unit-test-not-a-real-key",
        nvidia_model="nvidia/nemotron-3-super-120b-a12b",
        nvidia_fallback_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        nvidia_max_retries=3,
        nvidia_retry_base_delay=0.01,
        nvidia_retry_max_delay=0.05,
    )


@pytest.fixture
def sleep_calls(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture backoff sleeps instead of waiting them out."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("app.llm.nvidia_provider.asyncio.sleep", fake_sleep)
    return recorded


def make_provider(settings: Settings, script: list[Any]) -> tuple[NvidiaProvider, StubClient]:
    client = StubClient(script)
    return NvidiaProvider(settings, client=client), client


# --------------------------------------------------------------------------- #
# Think-block stripping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>let me weigh this</think>Do the work.", "Do the work."),
        ("<THINK>caps</THINK>Answer.", "Answer."),
        ("<thinking>variant tag</thinking>Answer.", "Answer."),
        ("<think>one</think>A.<think>two</think>B.", "A.B."),
        ("<think>multi\nline\nreasoning</think>\n\nBG-2.47 says...", "BG-2.47 says..."),
        ("Answer first.<think>truncated trace with no close", "Answer first."),
        ("</think>Only a stray closing tag.", "Only a stray closing tag."),
        ("No tags at all.", "No tags at all."),
        ("", ""),
    ],
)
def test_strip_think_blocks(raw: str, expected: str) -> None:
    assert strip_think_blocks(raw) == expected


async def test_reasoning_is_stripped_from_the_completion(nvidia_settings: Settings) -> None:
    provider, _ = make_provider(
        nvidia_settings,
        [StubCompletion("<think>Arjuna is grieving, so...</think>BG-2.47 answers this.")],
    )
    response = await provider.complete(ANSWER_PROMPT)
    assert response.text == "BG-2.47 answers this."
    assert "<think>" not in response.text
    assert response.raw["stripped_reasoning"] is True


async def test_thinking_is_disabled_both_ways(nvidia_settings: Settings) -> None:
    """The chat-template flag *and* the prompt directive are both sent."""
    provider, client = make_provider(nvidia_settings, [StubCompletion("BG-2.47 ...")])
    await provider.complete(ANSWER_PROMPT, system="You are GitaGuide.")

    kwargs = client.calls[0]
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"thinking": False}}
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][0]["content"].startswith("/no_think")
    assert "You are GitaGuide." in kwargs["messages"][0]["content"]


async def test_thinking_can_be_left_on(nvidia_settings: Settings) -> None:
    provider = NvidiaProvider(
        nvidia_settings, client=StubClient([StubCompletion("x")]), disable_thinking=False
    )
    await provider.complete(ANSWER_PROMPT, system="sys")
    call = provider._client.calls[0]  # type: ignore[union-attr]
    assert "extra_body" not in call
    assert call["messages"][0]["content"] == "sys"


async def test_template_kwargs_rejection_degrades_gracefully(
    nvidia_settings: Settings, sleep_calls: list[float]
) -> None:
    """A 400 about chat_template_kwargs retries immediately without the flag."""
    provider, client = make_provider(
        nvidia_settings,
        [
            StubError(400, "unrecognised argument chat_template_kwargs"),
            StubCompletion("BG-2.47 answers this."),
        ],
    )
    response = await provider.complete(ANSWER_PROMPT)

    assert response.text == "BG-2.47 answers this."
    assert "extra_body" in client.calls[0]
    assert "extra_body" not in client.calls[1], "flag must be dropped on retry"
    assert provider._template_kwargs_supported is False
    assert sleep_calls == [], "a rejected body should not cost backoff time"
    # The prompt directive still carries the instruction.
    assert client.calls[1]["messages"][0]["content"].startswith("/no_think")


# --------------------------------------------------------------------------- #
# Token budgets
# --------------------------------------------------------------------------- #
async def test_generation_defaults(nvidia_settings: Settings) -> None:
    provider, client = make_provider(nvidia_settings, [StubCompletion("BG-2.47 ...")])
    await provider.complete(ANSWER_PROMPT)
    assert client.calls[0]["temperature"] == pytest.approx(0.6)
    assert client.calls[0]["max_tokens"] == 1200


async def test_intent_calls_are_clamped(nvidia_settings: Settings) -> None:
    """Intent classification must stay at or below the compact 128-token ceiling."""
    provider, client = make_provider(nvidia_settings, [StubCompletion('{"intent":"other"}')])
    await provider.complete(INTENT_PROMPT, max_tokens=900)
    assert client.calls[0]["max_tokens"] <= 128
    assert client.calls[0]["max_tokens"] == nvidia_settings.nvidia_intent_max_tokens


async def test_explicit_overrides_are_respected(nvidia_settings: Settings) -> None:
    provider, client = make_provider(nvidia_settings, [StubCompletion("x")])
    await provider.complete(ANSWER_PROMPT, max_tokens=300, temperature=0.1, stop=["END"])
    assert client.calls[0]["max_tokens"] == 300
    assert client.calls[0]["temperature"] == pytest.approx(0.1)
    assert client.calls[0]["stop"] == ["END"]


def test_intent_prompt_detection() -> None:
    assert is_intent_prompt(INTENT_PROMPT)
    assert not is_intent_prompt(ANSWER_PROMPT)


# --------------------------------------------------------------------------- #
# Retry behaviour
# --------------------------------------------------------------------------- #
async def test_429_then_success(nvidia_settings: Settings, sleep_calls: list[float]) -> None:
    provider, client = make_provider(
        nvidia_settings, [StubError(429, "rate limit"), StubCompletion("BG-2.47 ...")]
    )
    response = await provider.complete(ANSWER_PROMPT)

    assert response.text == "BG-2.47 ..."
    assert len(client.calls) == 2
    assert response.raw["attempts"] == 2
    assert len(sleep_calls) == 1


async def test_retry_after_header_is_honoured(
    nvidia_settings: Settings, sleep_calls: list[float]
) -> None:
    provider, _ = make_provider(
        nvidia_settings,
        [StubError(429, "slow down", retry_after="7"), StubCompletion("ok")],
    )
    await provider.complete(ANSWER_PROMPT)
    assert sleep_calls == [7.0], "Retry-After must win over the backoff schedule"


async def test_backoff_is_exponential(nvidia_settings: Settings, sleep_calls: list[float]) -> None:
    settings = nvidia_settings.model_copy(
        update={"nvidia_retry_base_delay": 1.0, "nvidia_retry_max_delay": 60.0}
    )
    provider, _ = make_provider(
        settings, [StubError(429), StubError(503), StubCompletion("ok")]
    )
    await provider.complete(ANSWER_PROMPT)
    assert sleep_calls == [1.0, 2.0]


async def test_retries_are_capped_then_raise(
    nvidia_settings: Settings, sleep_calls: list[float]
) -> None:
    provider, client = make_provider(
        nvidia_settings, [StubError(429), StubError(429), StubError(429)]
    )
    with pytest.raises(LLMError, match=r"after 3 attempt\(s\) \(retryable"):
        await provider.complete(ANSWER_PROMPT)

    assert len(client.calls) == 3, "exactly nvidia_max_retries attempts"
    assert len(sleep_calls) == 2, "no sleep after the final attempt"


async def test_5xx_is_retried(nvidia_settings: Settings, sleep_calls: list[float]) -> None:
    provider, client = make_provider(
        nvidia_settings, [StubError(503, "upstream busy"), StubCompletion("ok")]
    )
    assert (await provider.complete(ANSWER_PROMPT)).text == "ok"
    assert len(client.calls) == 2


async def test_auth_error_is_not_retried(
    nvidia_settings: Settings, sleep_calls: list[float]
) -> None:
    """A 403 is permanent: fail fast so failover happens immediately."""
    provider, client = make_provider(nvidia_settings, [StubError(403, "Authorization failed")])
    with pytest.raises(LLMError, match=r"after 1 attempt\(s\) \(permanent"):
        await provider.complete(ANSWER_PROMPT)
    assert len(client.calls) == 1
    assert sleep_calls == []


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses(status: int) -> None:
    assert is_retryable(StubError(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_statuses(status: int) -> None:
    assert not is_retryable(StubError(status))


def test_transport_errors_are_retryable() -> None:
    class APIConnectionError(Exception):
        pass

    assert is_retryable(APIConnectionError("connection reset"))


def test_error_status_reads_a_nested_response() -> None:
    class Wrapped(Exception):
        response = type("R", (), {"status_code": 429, "headers": {"retry-after": "3"}})()

    exc = Wrapped("rate limited")
    assert error_status(exc) == 429
    assert retry_after_seconds(exc) == 3.0


def test_backoff_delay_is_capped() -> None:
    assert backoff_delay(1, 1.0, 20.0) == 1.0
    assert backoff_delay(2, 1.0, 20.0) == 2.0
    assert backoff_delay(3, 1.0, 20.0) == 4.0
    assert backoff_delay(9, 1.0, 20.0) == 20.0


def test_bad_retry_after_falls_back_to_backoff() -> None:
    assert retry_after_seconds(StubError(429, retry_after="not-a-number")) is None


# --------------------------------------------------------------------------- #
# Failover chain
# --------------------------------------------------------------------------- #
def test_chain_order_is_super_then_lightning(nvidia_settings: Settings) -> None:
    chain = build_nvidia_chain(nvidia_settings)
    assert [provider.name for provider in chain] == [
        "nvidia:nvidia/nemotron-3-super-120b-a12b",
        "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b",
    ]


def test_the_live_chain_has_no_fake_last_resort(nvidia_settings: Settings) -> None:
    """A total outage must not be served to a tester as guidance.

    The fake always answers, and answers with citations, so it used to make an
    outage look like a real reply. The chain now ends at the real providers and
    the generation stage says "the guide is overloaded" instead - which is
    honest, and countable.
    """
    assert not any(provider.is_fake for provider in build_nvidia_chain(nvidia_settings))


def test_a_configured_extra_fallback_is_appended_after_the_nim_models(
    nvidia_settings: Settings,
) -> None:
    settings = nvidia_settings.model_copy(
        update={"llm_fallback_provider": LLMProviderName.OPENAI, "openai_api_key": ""}
    )
    # No OpenAI key, so that link is skipped and the NIM pair is the whole chain.
    chain = build_nvidia_chain(settings)
    assert [provider.name for provider in chain] == [
        "nvidia:nvidia/nemotron-3-super-120b-a12b",
        "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b",
    ]


def test_duplicate_fallback_model_is_not_added(nvidia_settings: Settings) -> None:
    settings = nvidia_settings.model_copy(
        update={"nvidia_fallback_model": nvidia_settings.nvidia_model}
    )
    assert len(build_nvidia_chain(settings)) == 1


async def test_failover_reaches_the_nvidia_fallback_model(nvidia_settings: Settings) -> None:
    """Persistent 429 on Super hands off to Lightning, not straight to fake."""
    super_provider = NvidiaProvider(
        nvidia_settings,
        model="nvidia/nemotron-3-super-120b-a12b",
        client=StubClient([StubError(429), StubError(429), StubError(429)]),
    )
    lightning_provider = NvidiaProvider(
        nvidia_settings,
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
        client=StubClient([StubCompletion("BG-2.47 from Lightning.")]),
    )
    chain = FallbackLLMProvider([super_provider, lightning_provider, FakeLLMProvider()])

    response = await chain.complete(ANSWER_PROMPT)
    assert response.text == "BG-2.47 from Lightning."
    assert response.model == "nvidia/nemotron-3.5-lightning-30b-a3b"


async def test_failover_reaches_the_fake_when_all_nim_models_fail(
    nvidia_settings: Settings,
) -> None:
    dead = [
        NvidiaProvider(
            nvidia_settings,
            model=model,
            client=StubClient([StubError(500), StubError(500), StubError(500)]),
        )
        for model in (
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        )
    ]
    chain = FallbackLLMProvider([*dead, FakeLLMProvider()])
    response = await chain.complete(ANSWER_PROMPT)
    assert response.provider == "fake"
    assert response.text, "the offline provider must still answer"


async def test_retries_happen_before_failover(nvidia_settings: Settings) -> None:
    """A momentary 429 must be retried on Super, not skipped to Lightning."""
    super_client = StubClient([StubError(429), StubCompletion("BG-2.47 from super.")])
    nano_client = StubClient([StubCompletion("should not be reached")])
    chain = FallbackLLMProvider(
        [
            NvidiaProvider(nvidia_settings, model="super", client=super_client),
            NvidiaProvider(nvidia_settings, model="nano", client=nano_client),
        ]
    )
    response = await chain.complete(ANSWER_PROMPT)
    assert response.text == "BG-2.47 from super."
    assert len(nano_client.calls) == 0


# --------------------------------------------------------------------------- #
# Wiring / configuration
# --------------------------------------------------------------------------- #
def test_nvidia_is_the_default_provider_when_a_key_is_present() -> None:
    settings = Settings(mock_mode=False, nvidia_api_key="nvapi-x", llm_provider=LLMProviderName.FAKE)
    assert settings.resolved_llm_provider is LLMProviderName.NVIDIA
    assert settings.nvidia_enabled


def test_mock_mode_always_wins() -> None:
    settings = Settings(
        mock_mode=True, nvidia_api_key="nvapi-x", llm_provider=LLMProviderName.NVIDIA
    )
    assert settings.resolved_llm_provider is LLMProviderName.FAKE
    assert not settings.nvidia_enabled


def test_missing_key_degrades_to_fake() -> None:
    settings = Settings(
        mock_mode=False, nvidia_api_key="", llm_provider=LLMProviderName.NVIDIA
    )
    assert settings.resolved_llm_provider is LLMProviderName.FAKE


def test_explicit_provider_with_its_key_is_honoured() -> None:
    settings = Settings(
        mock_mode=False,
        nvidia_api_key="nvapi-x",
        anthropic_api_key="sk-ant-x",
        llm_provider=LLMProviderName.ANTHROPIC,
    )
    assert settings.resolved_llm_provider is LLMProviderName.ANTHROPIC


def test_build_llm_provider_returns_the_nvidia_chain(nvidia_settings: Settings) -> None:
    provider = build_llm_provider(nvidia_settings)
    assert isinstance(provider, FallbackLLMProvider)
    assert provider.name.startswith("fallback(nvidia:")
    assert not any(step.is_fake for step in provider.providers)
    # The wall-clock budget must be attached here, not left to each provider's
    # own timeout - otherwise two "reasonable" timeouts add up to a long wait.
    assert provider.budget_seconds == nvidia_settings.generation_budget_seconds


def test_build_llm_provider_is_fake_in_mock_mode() -> None:
    assert build_llm_provider(Settings(mock_mode=True)).is_fake


def test_provider_without_a_key_refuses_to_construct() -> None:
    settings = Settings(mock_mode=False, nvidia_api_key="")
    with pytest.raises(LLMError, match="NVIDIA_API_KEY"):
        NvidiaProvider(settings)


async def test_aclose_closes_the_client(nvidia_settings: Settings) -> None:
    provider, client = make_provider(nvidia_settings, [])
    await provider.aclose()
    assert client.closed


# --------------------------------------------------------------------------- #
# Response mapping
# --------------------------------------------------------------------------- #
async def test_usage_and_metadata_are_recorded(nvidia_settings: Settings) -> None:
    provider, _ = make_provider(nvidia_settings, [StubCompletion("BG-2.47 ...")])
    response = await provider.complete(ANSWER_PROMPT)

    assert response.provider == "nvidia:nvidia/nemotron-3-super-120b-a12b"
    assert response.model == "nvidia/nemotron-3-super-120b-a12b"
    assert response.prompt_tokens == 120
    assert response.completion_tokens == 60
    assert response.latency_ms >= 0
    assert response.raw["finish_reason"] == "stop"
    assert response.raw["thinking_disabled"] is True


async def test_truncated_response_is_still_returned(nvidia_settings: Settings) -> None:
    provider, _ = make_provider(
        nvidia_settings, [StubCompletion("BG-2.47 says your right is to", "length")]
    )
    response = await provider.complete(ANSWER_PROMPT)
    assert response.raw["finish_reason"] == "length"
    assert response.text


async def test_intent_json_survives_the_round_trip(nvidia_settings: Settings) -> None:
    """A think-wrapped JSON payload must still parse for the classifier."""
    provider, _ = make_provider(
        nvidia_settings,
        [StubCompletion('<think>work related</think>{"intent":"professional_dilemma"}')],
    )
    response = await provider.complete(INTENT_PROMPT)
    assert response.json()["intent"] == "professional_dilemma"
