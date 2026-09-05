"""NVIDIA NIM (build.nvidia.com) implementation of :class:`LLMProvider`.

Research-pipeline role
---------------------
This is GitaGuide's primary generator. NIM exposes an OpenAI-compatible
``/chat/completions`` endpoint, so the ``openai`` SDK is reused with a different
``base_url``; nothing in the pipeline changes.

Three NIM-specific concerns are handled here, and nowhere else:

**Hybrid reasoning.** Nemotron 3 can emit a reasoning trace. A guidance answer
must not contain one, so thinking is disabled *and* the output is cleaned:

1. ``extra_body={"chat_template_kwargs": {"thinking": false}}`` - the chat
   template flag NIM passes through to the model;
2. a ``/no_think`` directive prepended to the system prompt, for builds that read
   the switch from the prompt instead;
3. defensive stripping of ``<think>...</think>`` (and an unterminated ``<think>``
   left by a truncated response) plus any ``reasoning_content`` field.

If the endpoint rejects the ``chat_template_kwargs`` body, the request is retried
once without it and the flag is remembered, so a template change upstream
degrades to (2) + (3) rather than failing.

**Free-tier rate limits.** ~40 requests/minute per model, HTTP 429 on burst.
Retries happen *inside* this provider (exponential backoff, ``Retry-After``
honoured) so a momentary 429 does not cause
:class:`app.llm.base.FallbackLLMProvider` to skip to a weaker model. Only after
the retry budget is exhausted does failover advance.

**Token budgets.** Generation defaults to ``temperature=0.6`` /
``max_tokens=1200``; intent-classification prompts (identified by the
``TASK: intent_classification`` marker every prompt builder writes) are clamped
to ``nvidia_intent_max_tokens`` (128) because that call returns only compact JSON.

The ``openai`` SDK is imported lazily inside :meth:`NvidiaProvider._get_client`,
so importing this module - and unit-testing it with an injected client - needs
neither the package nor a key.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from app import telemetry
from app.config import Settings, get_settings
from app.llm.base import TASK_INTENT, LLMError, LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

#: ``<think> ... </think>`` (any casing, across newlines), plus the ``<thinking>``
#: variant some NIM builds emit.
THINK_BLOCK_RE = re.compile(r"<(think|thinking)>.*?</\1>", re.IGNORECASE | re.DOTALL)
#: An opening tag with no closing tag - what a truncated reasoning trace leaves.
UNCLOSED_THINK_RE = re.compile(r"<(think|thinking)>.*\Z", re.IGNORECASE | re.DOTALL)
#: Stray closing tag when the model started mid-trace.
ORPHAN_CLOSE_RE = re.compile(r"^\s*</(think|thinking)>", re.IGNORECASE)

#: HTTP statuses worth retrying: rate limit, and anything server-side.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
#: Statuses that mean "the request body was wrong", used to detect a rejected
#: ``chat_template_kwargs`` payload.
BAD_REQUEST_STATUSES = frozenset({400, 422})


def strip_think_blocks(text: str) -> str:
    """Remove reasoning traces from a completion, defensively.

    Handles complete blocks, an unterminated opening tag (truncation), and a
    stray closing tag. Always returns the *answer* part.

    >>> strip_think_blocks("<think>weighing it up</think>Do the work.")
    'Do the work.'
    >>> strip_think_blocks("Answer first.<think>truncated reasoning")
    'Answer first.'
    """
    if not text:
        return ""
    cleaned = THINK_BLOCK_RE.sub("", text)
    cleaned = ORPHAN_CLOSE_RE.sub("", cleaned)
    cleaned = UNCLOSED_THINK_RE.sub("", cleaned)
    return cleaned.strip()


def is_intent_prompt(prompt: str) -> bool:
    """True for the intent-classification task (see :mod:`app.llm.base`)."""
    return f"TASK: {TASK_INTENT}" in prompt


def error_status(exc: BaseException) -> int | None:
    """Best-effort HTTP status from an SDK exception.

    Duck-typed on purpose: it works with ``openai.APIStatusError``, with an
    ``httpx.HTTPStatusError``, and with the lightweight stubs the unit tests
    inject - so nothing here imports a vendor exception class.
    """
    for attribute in ("status_code", "status", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _headers_of(exc: BaseException) -> Mapping[str, Any]:
    for holder in (exc, getattr(exc, "response", None)):
        headers = getattr(holder, "headers", None)
        if isinstance(headers, Mapping):
            return headers
    return {}


def retry_after_seconds(exc: BaseException) -> float | None:
    """``Retry-After`` in seconds, if the server sent one."""
    headers = _headers_of(exc)
    for key in ("retry-after", "Retry-After", "x-ratelimit-reset-after"):
        raw = headers.get(key) if hasattr(headers, "get") else None
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff for a 1-indexed attempt number."""
    return min(cap, base * (2 ** max(0, attempt - 1)))


def is_retryable(exc: BaseException) -> bool:
    """Retry on rate limits, server errors and transport timeouts."""
    status = error_status(exc)
    if status is not None:
        return status in RETRYABLE_STATUSES
    # No status: connection reset / timeout / DNS. Worth one more try.
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "connection", "apiconnection"))


def mentions_template_kwargs(exc: BaseException) -> bool:
    """True when a 400/422 blames ``chat_template_kwargs``."""
    if error_status(exc) not in BAD_REQUEST_STATUSES:
        return False
    return "chat_template_kwargs" in str(exc) or "thinking" in str(exc).lower()


class NvidiaProvider(LLMProvider):
    """NVIDIA NIM chat completions, with in-provider retry and think stripping.

    Parameters
    ----------
    model:
        NIM model slug. Defaults to ``settings.nvidia_model``; the failover
        chain builds a second instance with ``settings.nvidia_fallback_model``.
    client:
        Injected async client (unit tests). When omitted, an
        ``openai.AsyncOpenAI`` pointed at ``settings.nvidia_base_url`` is created
        lazily on first use.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        client: Any | None = None,
        disable_thinking: bool | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.model = model or self.settings.nvidia_model
        #: Traces show which NIM model actually answered.
        self.name = f"nvidia:{self.model}"
        self.disable_thinking = (
            self.settings.nvidia_disable_thinking if disable_thinking is None else disable_thinking
        )
        self._client = client
        #: Set if the endpoint rejects the chat-template flag; see module docstring.
        self._template_kwargs_supported = True

        if client is None and not self.settings.nvidia_api_key:
            raise LLMError(
                "NVIDIA_API_KEY is not set (put it in .env; it is never read from code)"
            )

    # ------------------------------------------------------------- plumbing
    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI  # lazy: keeps the tests SDK-free
            except ImportError as exc:  # pragma: no cover - environment issue
                raise LLMError(
                    "the openai SDK is required for NVIDIA NIM (pip install openai); "
                    "NIM speaks the OpenAI wire format"
                ) from exc

            self._client = AsyncOpenAI(
                api_key=self.settings.nvidia_api_key,
                base_url=self.settings.nvidia_base_url,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=0,  # retries are ours, so Retry-After is honoured
            )
        return self._client

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        if client is not None and hasattr(client, "close"):
            await client.close()

    # -------------------------------------------------------------- request
    def _resolve_limits(
        self,
        prompt: str,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[int, float]:
        """Generation defaults, with the intent-classification clamp applied."""
        tokens = max_tokens or self.settings.nvidia_max_tokens
        if is_intent_prompt(prompt):
            tokens = min(tokens, self.settings.nvidia_intent_max_tokens)
        degrees = self.settings.nvidia_temperature if temperature is None else temperature
        return tokens, degrees

    def _build_messages(self, prompt: str, system: str | None) -> list[dict[str, str]]:
        """System (with the thinking directive) + user."""
        directive = self.settings.nvidia_thinking_directive if self.disable_thinking else ""
        system_text = "\n\n".join(part for part in (directive, system or "") if part).strip()
        messages: list[dict[str, str]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_kwargs(
        self,
        prompt: str,
        system: str | None,
        max_tokens: int | None,
        temperature: float | None,
        stop: Sequence[str] | None,
    ) -> dict[str, Any]:
        tokens, degrees = self._resolve_limits(prompt, max_tokens, temperature)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(prompt, system),
            "max_tokens": tokens,
            "temperature": degrees,
        }
        if stop:
            kwargs["stop"] = list(stop)
        if self.disable_thinking and self._template_kwargs_supported:
            kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
        return kwargs

    # ------------------------------------------------------------- response
    def _to_response(self, completion: Any, attempts: int, started: float) -> LLMResponse:
        choice = completion.choices[0]
        message = getattr(choice, "message", None)
        raw_text = getattr(message, "content", "") or ""
        text = strip_think_blocks(raw_text)

        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            logger.warning(
                "NIM response truncated (model=%s): raise max_tokens if this recurs",
                self.model,
            )
        if not text and raw_text:
            logger.warning(
                "NIM response was reasoning-only after stripping (model=%s)", self.model
            )

        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            raw={
                "finish_reason": finish_reason,
                "attempts": attempts,
                "thinking_disabled": self.disable_thinking,
                "template_kwargs_sent": self.disable_thinking and self._template_kwargs_supported,
                "stripped_reasoning": raw_text != text,
            },
        )

    # ------------------------------------------------------------ interface
    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        """One completion, retrying 429/5xx before giving up on this model."""
        client = self._get_client()
        attempts = max(1, self.settings.nvidia_max_retries)
        started = time.perf_counter()
        last_error: BaseException | None = None
        made = 0

        for attempt in range(1, attempts + 1):
            made = attempt
            kwargs = self._build_kwargs(prompt, system, max_tokens, temperature, stop)
            try:
                completion = await client.chat.completions.create(**kwargs)
                return self._to_response(completion, attempt, started)
            except Exception as exc:  # classified by status code just below
                last_error = exc

                # The chat-template flag was rejected: drop it and try again
                # immediately (this attempt does not consume backoff).
                if mentions_template_kwargs(exc) and self._template_kwargs_supported:
                    logger.warning(
                        "NIM rejected chat_template_kwargs for %s; "
                        "falling back to the prompt directive",
                        self.model,
                    )
                    self._template_kwargs_supported = False
                    continue

                if not is_retryable(exc) or attempt == attempts:
                    break

                # Upstream pressure is worth counting: a run where Super
                # returned 503 twenty times explains its own latency, and the
                # Day 3 baseline recorded zero of them, so a change is visible.
                upstream_status = error_status(exc)
                telemetry.count("upstream_retry")
                if upstream_status is not None:
                    telemetry.count(f"upstream_{upstream_status}")

                delay = retry_after_seconds(exc)
                if delay is None:
                    delay = backoff_delay(
                        attempt,
                        self.settings.nvidia_retry_base_delay,
                        self.settings.nvidia_retry_max_delay,
                    )
                logger.warning(
                    "NIM %s attempt %d/%d failed (status=%s); retrying in %.1fs",
                    self.model,
                    attempt,
                    attempts,
                    error_status(exc),
                    delay,
                )
                await asyncio.sleep(delay)

        status = error_status(last_error) if last_error else None
        retried = "retryable" if last_error and is_retryable(last_error) else "permanent"
        raise LLMError(
            f"nvidia nim completion failed for {self.model} after {made} attempt(s) "
            f"({retried}, status={status}): {last_error}"
        ) from last_error
