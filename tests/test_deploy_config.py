"""The deployment config must stay consistent with the code's assumptions.

Two of those assumptions are invisible at runtime and expensive to get wrong:

* **The rate limiter and telemetry counters are in-process.** They live in one
  process's memory, so a second worker or a second instance does not share
  them - it silently *multiplies* every configured limit by the worker count.
  Nothing errors. The service looks fine and quietly spends several times the
  NIM budget it was sized for.
* **The per-IP limit reads ``X-Forwarded-For``**, which only means anything if
  the number of trusted proxies is declared. Behind a proxy with the default of
  zero, every request keys on the proxy's own address and the per-IP bucket
  collapses into a second global one.

Neither shows up in a smoke test - the production limiter looked broken for a
completely different reason (a sequential burst cannot drain a token bucket
when each request takes longer than the refill interval), and while chasing
that, a worker bump would have been indistinguishable. So the config is asserted
here, where changing it fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
RENDER_YAML = ROOT / "render.yaml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def render_yaml() -> str:
    return RENDER_YAML.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# One worker
# --------------------------------------------------------------------------- #
def test_the_production_start_command_runs_exactly_one_worker(dockerfile: str) -> None:
    """A worker bump must fail here rather than silently doubling every limit."""
    cmd = next(
        (line for line in dockerfile.splitlines() if line.startswith("CMD") and "uvicorn" in line),
        None,
    )
    assert cmd is not None, "no uvicorn CMD found in the Dockerfile"

    workers = re.search(r"--workers\s+(\d+)", cmd)
    assert workers is not None, (
        "the start command does not pin --workers. The rate limiter and telemetry "
        "counters are in-process, so an unpinned worker count means the effective "
        "limits depend on however many workers uvicorn happens to start."
    )
    assert workers.group(1) == "1", (
        f"--workers is {workers.group(1)}, but the rate limiter is in-process "
        "(app/api/ratelimit.py) and so are the telemetry counters "
        "(app/telemetry.py). N workers means N independent limiters and N times "
        "the configured rate. Either keep this at 1, or move both to shared "
        "storage first and then update this test."
    )


def test_gunicorn_is_not_used(dockerfile: str) -> None:
    """gunicorn's default is multiple workers, which would break the same thing."""
    assert "gunicorn" not in dockerfile.lower(), (
        "gunicorn defaults to more than one worker; see the test above for why "
        "that is not safe while the limiter is in-process"
    )


def test_render_does_not_override_the_start_command(render_yaml: str) -> None:
    """An override in render.yaml would silently win over the Dockerfile CMD."""
    for key in ("dockerCommand", "startCommand"):
        assert key not in render_yaml, (
            f"render.yaml sets {key}, which overrides the Dockerfile CMD and "
            "therefore bypasses the single-worker guarantee asserted above"
        )


def test_render_runs_a_single_instance(render_yaml: str) -> None:
    """Scaling out multiplies the limits exactly as extra workers would."""
    instances = re.search(r"numInstances:\s*(\d+)", render_yaml)
    assert instances is not None, "render.yaml does not pin numInstances"
    assert instances.group(1) == "1", (
        f"numInstances is {instances.group(1)}; the in-process limiter cannot "
        "span instances any more than it can span workers"
    )


# --------------------------------------------------------------------------- #
# Proxy hops
# --------------------------------------------------------------------------- #
def test_production_declares_its_proxy_hop_count(render_yaml: str) -> None:
    """Behind Render there is exactly one proxy, and it has to be declared.

    Left at the default of 0, the per-IP limit keys on Render's own address for
    every request and stops being a per-IP limit at all.
    """
    match = re.search(
        r'GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS\s*\n\s*value:\s*"?(\d+)"?', render_yaml
    )
    assert match is not None, (
        "render.yaml does not set GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS; the "
        "per-IP limit would key on the proxy address"
    )
    assert match.group(1) == "1", (
        f"trusted proxy hops is {match.group(1)}. Render puts exactly one proxy "
        "in front of the container. A larger number lets a client choose its own "
        "rate-limit bucket by forging earlier X-Forwarded-For entries."
    )


def test_the_default_is_to_trust_no_proxy_headers() -> None:
    """Fail safe: an undeclared deployment must not believe the header."""
    from app.config import Settings

    assert Settings(mock_mode=True).rate_limit_trusted_proxy_hops == 0
