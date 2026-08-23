"""Anthropic implementation of :class:`app.llm.base.LLMProvider`.

The ``anthropic`` SDK is imported lazily so the package remains an optional
dependency for offline development and CI.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Claude via the official async SDK."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None, model: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = model or self.settings.anthropic_model
        if not self.settings.anthropic_api_key:
            raise LLMError("GITAGUIDE_ANTHROPIC_API_KEY is not set")
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.llm_timeout_seconds,
            )
        return self._client

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "temperature": (
                self.settings.llm_temperature if temperature is None else temperature
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if stop:
            kwargs["stop_sequences"] = list(stop)

        try:
            message = await client.messages.create(**kwargs)
        except Exception as exc:  # pragma: no cover - needs a live API
            raise LLMError(f"anthropic completion failed: {exc}") from exc

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(message, "usage", None)
        return LLMResponse(
            text=text.strip(),
            provider=self.name,
            model=self.model,
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            raw={"stop_reason": getattr(message, "stop_reason", None)},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
