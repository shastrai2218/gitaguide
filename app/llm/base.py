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

import asyncio
import json
import logging
import re
import time
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


class BudgetExhaustedError(LLMError):
    """The turn ran out of wall clock before any provider answered.

    Distinct from :class:`LLMError` so the generator can tell "everything is
    broken" apart from "everything is slow" and say the honest thing to the
    tester about which one happened.
    """


class FallbackLLMProvider(LLMProvider):
    """Try providers in order until one succeeds, inside a wall-clock budget.

    Configured via ``GITAGUIDE_LLM_PROVIDER`` + ``GITAGUIDE_LLM_FALLBACK_PROVIDER``
    so a transient Anthropic outage degrades to OpenAI (or to the fake provider
    in development) instead of failing the request.

    **The budget spans the whole chain, not each provider.** Without that, a
    primary that retries 429s for its full backoff and then hands over to a
    fallback that does the same can keep one tester waiting for minutes while
    every individual timeout looks reasonable. The budget is what a person
    actually experiences, so it is what is enforced.

    It is also **shared**, not spent first-come: each attempt may use at most
    ``remaining / providers_left``. A primary allowed to burn the whole budget
    would starve the fallback of the time it needs to answer, which defeats the
    point of having one - the fallback exists precisely for the case where the
    primary is slow. With a 45s budget and a Super/Lightning chain, Super gets
    ~22s (worst observed turn: 13.6s) and Lightning keeps the rest (observed
    ~21.1s). Only when every provider has had its slice is the caller told the
    guide is overloaded.
    """

    name = "fallback"

    def __init__(
        self, providers: Sequence[LLMProvider], budget_seconds: float | None = None
    ) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider needs at least one provider")
        self.providers = list(providers)
        self.model = self.providers[0].model
        self.budget_seconds = budget_seconds
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
        started = time.monotonic()
        budget = self.budget_seconds

        for step, provider in enumerate(self.providers):
            remaining = None if budget is None else budget - (time.monotonic() - started)
            providers_left = len(self.providers) - step
            # Each attempt gets an equal share of what is left, so a slow
            # primary cannot consume the fallback's chance to answer.
            slice_seconds = None if remaining is None else remaining / providers_left
            if remaining is not None and remaining <= 0:
                logger.error(
                    "generation budget of %.1fs exhausted before %s could be tried",
                    budget,
                    provider.name,
                )
                raise BudgetExhaustedError(
                    f"generation budget of {budget:.1f}s exhausted after "
                    f"{step} provider(s); last error: {last_error}"
                )
            try:
                call = provider.complete(
                    prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop,
                )
                response = (
                    await call
                    if slice_seconds is None
                    else await asyncio.wait_for(call, timeout=slice_seconds)
                )
                # How far down the chain the answer came from. 0 = the primary
                # answered; anything higher means degradation the caller needs
                # to be able to see per-turn, not just infer from the chain
                # string printed at startup.
                response.raw["fallback_steps"] = step
                return response
            except TimeoutError as exc:
                last_error = exc
                logger.error(
                    "provider %s exceeded its %.1fs share of the generation budget "
                    "(%.1fs of %.1fs left)",
                    provider.name,
                    slice_seconds or 0.0,
                    remaining or 0.0,
                    budget or 0.0,
                )
            except Exception as exc:  # pragma: no cover - needs live providers
                last_error = exc
                logger.warning("provider %s failed (%s); trying next", provider.name, exc)

        if budget is not None and (time.monotonic() - started) >= budget:
            raise BudgetExhaustedError(
                f"generation budget of {budget:.1f}s exhausted; last error: {last_error}"
            )
        raise LLMError(f"all providers failed; last error: {last_error}")

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()

    @property
    def is_fake(self) -> bool:
        return all(provider.is_fake for provider in self.providers)
