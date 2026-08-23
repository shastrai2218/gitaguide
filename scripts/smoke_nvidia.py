"""Manual smoke test for the NVIDIA NIM provider - makes ONE real API call.

This is deliberately **not** a pytest file: the test suite never touches the
network. Run it by hand when you want to confirm the key, the model slug and the
thinking switch against the live endpoint.

Usage
-----
::

    python -m scripts.smoke_nvidia                    # one call to the primary model
    python -m scripts.smoke_nvidia --fallback         # use nvidia_fallback_model
    python -m scripts.smoke_nvidia --model nvidia/nemotron-3-nano-30b-a3b
    python -m scripts.smoke_nvidia --list-models      # catalogue (needs no key)
    python -m scripts.smoke_nvidia --grep nemotron    # filter the catalogue
    python -m scripts.smoke_nvidia --show-raw         # do not strip <think> blocks

Requirements: ``pip install openai`` and ``NVIDIA_API_KEY`` in ``.env``. The key
is read through :mod:`app.config` and is never printed - only a masked prefix.

Each run costs one request against the free tier (~40 requests/minute/model).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.llm.base import LLMError
from app.llm.nvidia_provider import NvidiaProvider, strip_think_blocks

DEFAULT_PROMPT = (
    "A reader says: my manager took credit for my work and I am furious. "
    "In three sentences, and citing BG-2.62 by that exact id, say what the verse "
    "observes about anger. Use only that verse."
)


def mask(secret: str) -> str:
    """``nvapi-abcd...wxyz`` - enough to identify the key, not to use it."""
    if not secret:
        return "<empty>"
    if len(secret) <= 14:
        return f"{secret[:6]}...({len(secret)} chars)"
    return f"{secret[:10]}...{secret[-4:]} ({len(secret)} chars)"


def list_models(settings: Settings, pattern: str | None) -> int:
    """Print the NIM catalogue. The endpoint is public, so no key is needed."""
    url = f"{settings.nvidia_base_url.rstrip('/')}/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    ids = sorted(str(item.get("id", "")) for item in payload.get("data", []))
    if pattern:
        needle = pattern.lower()
        ids = [item for item in ids if needle in item.lower()]

    print(f"{len(ids)} model(s) at {url}" + (f" matching {pattern!r}" if pattern else ""))
    for item in ids:
        marker = ""
        if item == settings.nvidia_model:
            marker = "  <- GITAGUIDE_NVIDIA_MODEL"
        elif item == settings.nvidia_fallback_model:
            marker = "  <- GITAGUIDE_NVIDIA_FALLBACK_MODEL"
        print(f"  {item}{marker}")

    for configured, label in (
        (settings.nvidia_model, "primary"),
        (settings.nvidia_fallback_model, "fallback"),
    ):
        if pattern is None and configured not in ids:
            print(f"\nWARNING: configured {label} model {configured!r} is not in the catalogue")
    return 0


async def smoke(settings: Settings, model: str, prompt: str, show_raw: bool) -> int:
    """One real completion; prints model, latency and the first 200 characters."""
    print("NVIDIA NIM smoke test")
    print(f"  base_url : {settings.nvidia_base_url}")
    print(f"  model    : {model}")
    print(f"  api key  : {mask(settings.nvidia_api_key)}")
    print(f"  thinking : {'disabled' if settings.nvidia_disable_thinking else 'enabled'}")
    print(f"  limits   : max_tokens={settings.nvidia_max_tokens} "
          f"temperature={settings.nvidia_temperature} retries={settings.nvidia_max_retries}")
    print()

    if not settings.nvidia_api_key:
        print("NVIDIA_API_KEY is empty. Put the nvapi- key in .env and retry.")
        return 2

    provider = NvidiaProvider(
        settings,
        model=model,
        disable_thinking=False if show_raw else None,
    )
    started = time.perf_counter()
    try:
        response = await provider.complete(
            prompt,
            system="You are GitaGuide. Answer only from the verse you are given, and cite it.",
        )
    except LLMError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"FAILED after {elapsed:.0f} ms\n  {exc}")
        print(
            "\nHints: 403 = bad or placeholder key; 404 = wrong model slug "
            "(run --list-models); 429 = free-tier rate limit, wait a minute."
        )
        return 1
    finally:
        await provider.aclose()

    text = response.text if not show_raw else strip_think_blocks(response.text) or response.text
    print(f"OK  model={response.model}")
    print(f"    latency        : {response.latency_ms:.0f} ms")
    print(f"    tokens         : prompt={response.prompt_tokens} "
          f"completion={response.completion_tokens}")
    print(f"    attempts       : {response.raw.get('attempts')}")
    print(f"    finish_reason  : {response.raw.get('finish_reason')}")
    print(f"    template flag  : {response.raw.get('template_kwargs_sent')}")
    print(f"    reasoning seen : {response.raw.get('stripped_reasoning')}")
    print()
    print("    first 200 chars:")
    print(f"    {text[:200]!r}")

    if "<think" in text.lower():
        print("\nWARNING: a reasoning trace survived - check the thinking switch.")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default=None, help="model slug to call")
    parser.add_argument(
        "--fallback", action="store_true", help="call GITAGUIDE_NVIDIA_FALLBACK_MODEL"
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--list-models", action="store_true", help="print the catalogue and exit")
    parser.add_argument("--grep", default=None, help="filter --list-models output")
    parser.add_argument(
        "--show-raw", action="store_true", help="leave thinking on, to inspect the trace"
    )
    args = parser.parse_args(argv)

    # mock_mode must be off for a live call, whatever .env says.
    settings = get_settings().model_copy(update={"mock_mode": False})

    if args.list_models:
        return list_models(settings, args.grep)

    model = args.model or (
        settings.nvidia_fallback_model if args.fallback else settings.nvidia_model
    )
    return asyncio.run(smoke(settings, model, args.prompt, args.show_raw))


if __name__ == "__main__":
    sys.exit(main())
