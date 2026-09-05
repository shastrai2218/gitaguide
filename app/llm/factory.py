"""Build the configured provider, with the resilient failover chain."""

from __future__ import annotations

import logging

from app.config import LLMProviderName, Settings, get_settings
from app.llm.base import FallbackLLMProvider, LLMError, LLMProvider
from app.llm.fake import FakeLLMProvider

logger = logging.getLogger(__name__)


def build_single_provider(
    name: LLMProviderName,
    settings: Settings,
    model: str | None = None,
) -> LLMProvider:
    """Instantiate one provider, or raise :class:`LLMError` if unusable."""
    if name is LLMProviderName.FAKE:
        return FakeLLMProvider()
    if name is LLMProviderName.NVIDIA:
        from app.llm.nvidia_provider import NvidiaProvider  # lazy: SDK optional

        return NvidiaProvider(settings, model=model)
    if name is LLMProviderName.ANTHROPIC:
        from app.llm.anthropic_provider import AnthropicProvider  # lazy: SDK optional

        return AnthropicProvider(settings, model=model)
    if name is LLMProviderName.OPENAI:
        from app.llm.openai_provider import OpenAIProvider  # lazy: SDK optional

        return OpenAIProvider(settings, model=model)
    raise LLMError(f"unknown provider {name!r}")


def build_nvidia_chain(settings: Settings) -> list[LLMProvider]:
    """``nemotron-super -> nvidia fallback model -> fake``.

    Both NIM entries are the same provider class with different model slugs,
    which matters on the free tier: the ~40 requests/minute limit is *per
    model*, so a rate-limited Super has a fresh budget on Lightning. Retries for
    429/5xx happen inside :class:`app.llm.nvidia_provider.NvidiaProvider`, so
    this chain only advances once a model is genuinely unavailable.
    """
    from app.llm.nvidia_provider import NvidiaProvider  # lazy: SDK optional

    chain: list[LLMProvider] = [NvidiaProvider(settings, model=settings.nvidia_model)]

    fallback_model = settings.nvidia_fallback_model.strip()
    if fallback_model and fallback_model != settings.nvidia_model:
        chain.append(NvidiaProvider(settings, model=fallback_model))

    # An explicitly configured non-NIM fallback sits between NIM and the fake.
    extra = settings.llm_fallback_provider
    if extra is not None and extra not in {LLMProviderName.NVIDIA, LLMProviderName.FAKE}:
        try:
            chain.append(build_single_provider(extra, settings))
        except LLMError as exc:
            logger.warning("configured fallback provider %s unavailable (%s)", extra, exc)

    # Last resort: the offline provider. It always answers, and it answers with
    # citations, so a total outage degrades the answer instead of the service.
    chain.append(FakeLLMProvider())
    return chain


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Primary provider wrapped in a fallback chain.

    In mock mode (or with no usable key) this returns
    :class:`app.llm.fake.FakeLLMProvider`, so nothing in the pipeline needs to
    special-case offline development.
    """
    settings = settings or get_settings()
    primary_name = settings.resolved_llm_provider

    if primary_name is LLMProviderName.FAKE:
        return FakeLLMProvider()

    if primary_name is LLMProviderName.NVIDIA:
        try:
            chain = build_nvidia_chain(settings)
        except LLMError as exc:
            logger.warning("NVIDIA NIM unavailable (%s); using fake provider", exc)
            return FakeLLMProvider()
        logger.info(
            "LLM chain: %s", " -> ".join(provider.name for provider in chain)
        )
        return FallbackLLMProvider(chain)

    try:
        primary = build_single_provider(primary_name, settings)
    except LLMError as exc:
        logger.warning("primary provider %s unavailable (%s); using fake", primary_name, exc)
        return FakeLLMProvider()

    fallback_name = settings.llm_fallback_provider
    if fallback_name is None or fallback_name is primary_name:
        return primary

    try:
        fallback = build_single_provider(fallback_name, settings)
    except LLMError as exc:
        logger.warning("fallback provider %s unavailable (%s)", fallback_name, exc)
        return primary

    return FallbackLLMProvider([primary, fallback])
