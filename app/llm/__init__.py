"""LLM provider wrappers (vendor SDKs are imported lazily by each module)."""

from app.llm.base import (
    TASK_ANSWER,
    TASK_INTENT,
    TASK_SUMMARY,
    FallbackLLMProvider,
    LLMError,
    LLMProvider,
    LLMResponse,
)
from app.llm.factory import build_llm_provider, build_nvidia_chain, build_single_provider
from app.llm.fake import FakeLLMProvider

# Safe to import eagerly: the ``openai`` SDK itself is imported lazily inside
# NvidiaProvider._get_client, so this costs nothing and needs no key.
from app.llm.nvidia_provider import NvidiaProvider, strip_think_blocks

__all__ = [
    "TASK_ANSWER",
    "TASK_INTENT",
    "TASK_SUMMARY",
    "FakeLLMProvider",
    "FallbackLLMProvider",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "NvidiaProvider",
    "build_llm_provider",
    "build_nvidia_chain",
    "build_single_provider",
    "strip_think_blocks",
]
