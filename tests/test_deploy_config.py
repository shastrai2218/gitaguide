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

* **Turns can legitimately run for over a minute.** A 45s generation budget plus
  the intent call, retrieval and persistence, against a frontend that waits
  150s. Any process manager with a shorter request timeout kills the worker
  mid-turn, which the platform reports as a 502 with nothing in the application
  log to explain it - the request simply stops existing.

Neither of the first two shows up in a smoke test - the production limiter
looked broken for a completely different reason (a sequential burst cannot drain
a token bucket when each request takes longer than the refill interval), and
while chasing that, a worker bump would have been indistinguishable. So the
config is asserted here, where changing it fails loudly.
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


#: The longest a request may take before the process manager may kill it.
#:
#: Matches ``REQUEST_TIMEOUT_MS`` in ``frontend/src/api.js``: the server must not
#: give up before the client does, or the tester sees a 502 where they would
#: otherwise have seen either an answer or an honest timeout message.
MIN_REQUEST_TIMEOUT_SECONDS = 150


def test_the_start_command_allows_long_requests(dockerfile: str) -> None:
    """A turn can exceed a minute; the server must not cut it short.

    Plain uvicorn imposes no request deadline, so the pipeline's own generation
    budget stays the only thing deciding when a turn has taken too long. A
    process manager may be introduced later, but only with a timeout at least as
    long as the frontend is willing to wait.
    """
    cmd = next(
        (line for line in dockerfile.splitlines() if line.startswith("CMD") and "uvicorn" in line),
        None,
    )
    assert cmd is not None, "no uvicorn CMD found in the Dockerfile"

    if "gunicorn" in cmd:
        timeout = re.search(r"--timeout\s+(\d+)", cmd)
        assert timeout is not None, (
            "gunicorn is in the start command with no --timeout. Its default is "
            "30s, and a GitaGuide turn routinely exceeds that: the worker is "
            "killed mid-request and the platform reports a bare 502."
        )
        assert int(timeout.group(1)) >= MIN_REQUEST_TIMEOUT_SECONDS, (
            f"--timeout is {timeout.group(1)}s, below the "
            f"{MIN_REQUEST_TIMEOUT_SECONDS}s the frontend waits. The server would "
            "abandon turns the client is still waiting for."
        )
    else:
        # uvicorn has no equivalent flag; assert nothing has introduced one.
        for flag in ("--timeout ", "--timeout-graceful-shutdown"):
            if flag in cmd:
                value = re.search(re.escape(flag) + r"\s*(\d+)", cmd)
                assert value is not None and int(value.group(1)) >= MIN_REQUEST_TIMEOUT_SECONDS, (
                    f"{flag.strip()} in the start command is shorter than the "
                    f"{MIN_REQUEST_TIMEOUT_SECONDS}s the frontend waits"
                )


def test_gunicorn_is_not_used(dockerfile: str) -> None:
    """gunicorn's default is multiple workers, which would break the same thing.

    And its default 30s worker timeout would kill turns mid-flight. If it is
    ever adopted deliberately, both defaults have to be overridden and the test
    above enforces the timeout half.
    """
    # Only the executable lines matter - the comment above the CMD explains
    # why gunicorn is avoided, and must not itself trip this check.
    executable = [
        line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any("gunicorn" in line.lower() for line in executable), (
        "gunicorn defaults to more than one worker AND a 30s worker timeout; "
        "see the two tests above for why neither is safe here"
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
