"""OpenAI implementation of :class:`app.llm.base.LLMProvider`.

Exists so the dissertation can report results across two model families without
touching pipeline code. The SDK is imported lazily.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """GPT models via the official async SDK (chat completions)."""

    name = "openai"

    def __init__(self, settings: Settings | None = None, model: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = model or self.settings.openai_model
        if not self.settings.openai_api_key:
            raise LLMError("GITAGUIDE_OPENAI_API_KEY is not set")
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.settings.openai_api_key,
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
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            completion = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens or self.settings.llm_max_tokens,
                temperature=(
                    self.settings.llm_temperature if temperature is None else temperature
                ),
                stop=list(stop) if stop else None,
            )
        except Exception as exc:  # pragma: no cover - needs a live API
            raise LLMError(f"openai completion failed: {exc}") from exc

        choice = completion.choices[0]
        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            provider=self.name,
            model=self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            raw={"finish_reason": choice.finish_reason},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
