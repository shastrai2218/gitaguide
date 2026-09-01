"""Provider-agnostic LLM interface.

Research-pipeline role
---------------------
Three stages call an LLM: intent classification (zero-shot), grounded answer
generation, and conversation summarisation.  None of them import a vendor SDK.
They depend only on :class:`LLMProvider`, which means the whole pipeline can run
against :class:`app.llm.fake.FakeLLMProvider` with no API key, and the
dissertation can swap models without touching pipeline code.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Task markers embedded in prompts. The fake provider routes on these, and the
#: real providers simply pass them through as part of the prompt text.
TASK_INTENT = "intent_classification"
TASK_ANSWER = "grounded_answer"
TASK_SUMMARY = "conversation_summary"

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a completion."""


@dataclass(slots=True)
class LLMResponse:
    """Normalised completion result."""

    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self, default: dict[str, Any] | None = None) -> dict[str, Any]:
        """Best-effort JSON extraction from the completion body."""
        candidate = self.text.strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`")
            candidate = candidate.split("\n", 1)[-1] if "\n" in candidate else candidate
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            match = _JSON_OBJECT_RE.search(self.text)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        if default is not None:
            return default
        raise LLMError(f"provider {self.provider} did not return JSON: {self.text[:200]!r}")


class LLMProvider(ABC):
    """Minimal async completion interface implemented by every provider."""

    name: str = "abstract"
    model: str = "unknown"

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        """Return a single completion for ``prompt``."""

    async def aclose(self) -> None:
        """Release network resources (no-op for in-process providers)."""
        return None

    @property
    def is_fake(self) -> bool:
        return False


class FallbackLLMProvider(LLMProvider):
    """Try providers in order until one succeeds.

    Configured via ``GITAGUIDE_LLM_PROVIDER`` + ``GITAGUIDE_LLM_FALLBACK_PROVIDER``
    so a transient Anthropic outage degrades to OpenAI (or to the fake provider
    in development) instead of failing the request.
    """

    name = "fallback"

    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider needs at least one provider")
        self.providers = list(providers)
        self.model = self.providers[0].model
        self.name = "fallback(" + ",".join(p.name for p in self.providers) + ")"

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        last_error: Exception | None = None
        for step, provider in enumerate(self.providers):
            try:
                response = await provider.complete(
                    prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop,
                )
                # How far down the chain the answer came from. 0 = the primary
                # answered; anything higher means degradation the caller needs
                # to be able to see per-turn, not just infer from the chain
                # string printed at startup.
                response.raw["fallback_steps"] = step
                return response
            except Exception as exc:  # pragma: no cover - needs live providers
                last_error = exc
                logger.warning("provider %s failed (%s); trying next", provider.name, exc)
        raise LLMError(f"all providers failed; last error: {last_error}")

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()

    @property
    def is_fake(self) -> bool:
        return all(provider.is_fake for provider in self.providers)
