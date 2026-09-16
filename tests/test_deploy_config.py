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


# One worker
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


# Proxy hops
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


# Memory-critical settings
#
# These only work as a set. INT8 is chosen because 512 MB cannot hold the fp32
# encoder; but INT8 alone still leaves a single turn at 84% of the cap, so
# concurrent turns must also be prevented. Shipping one without the other looks
# fine in every test and dies under the first burst.
def _env_value(render_yaml: str, key: str) -> str | None:
    match = re.search(rf'{key}\s*\n\s*value:\s*"?([^"\n]+)"?', render_yaml)
    return match.group(1).strip() if match else None


def test_production_uses_the_int8_encoder(render_yaml: str) -> None:
    """The free tier cannot hold the fp32 encoder (497 MiB idle of 512)."""
    assert _env_value(render_yaml, "GITAGUIDE_EMBEDDER_PRECISION") == "int8", (
        "production must set GITAGUIDE_EMBEDDER_PRECISION=int8; the fp32 encoder "
        "leaves ~1.5 MiB of headroom on a 512 MB instance"
    )


def test_int8_in_production_requires_serialised_turns(render_yaml: str) -> None:
    """The pairing this test exists for.

    INT8 buys headroom for *one* turn (429 MiB peak of 512). Two concurrent
    turns exceed the cap, and the kernel's answer is SIGKILL - no traceback, a
    bare 502 and a restart. So if the deployment is on INT8 because memory is
    tight, it must also serialise; the two are one decision.
    """
    if _env_value(render_yaml, "GITAGUIDE_EMBEDDER_PRECISION") != "int8":
        pytest.skip("not an INT8 deployment; the pairing does not apply")

    assert _env_value(render_yaml, "GITAGUIDE_PIPELINE_SERIALISE") == "true", (
        "GITAGUIDE_EMBEDDER_PRECISION=int8 says this deployment is memory-bound, "
        "but GITAGUIDE_PIPELINE_SERIALISE is not true. INT8 leaves room for one "
        "turn, not two. Set both or neither."
    )


def test_production_pins_one_onnx_thread(render_yaml: str) -> None:
    """0.1 CPU cannot use a second intra-op thread; it only costs 47 MiB."""
    assert _env_value(render_yaml, "GITAGUIDE_ONNX_INTRA_OP_THREADS") == "1"


def test_the_queue_timeout_is_shorter_than_the_client_deadline(
    render_yaml: str,
) -> None:
    """A queued turn must be shed before the browser gives up on it.

    Otherwise the tester sees the frontend's timeout error for a request the
    server was still willing to serve, which is the confusing half of both
    failure modes at once.
    """
    timeout = _env_value(render_yaml, "GITAGUIDE_PIPELINE_QUEUE_TIMEOUT_SECONDS")
    assert timeout is not None, "the queue timeout must be explicit in production"
    assert float(timeout) < MIN_REQUEST_TIMEOUT_SECONDS, (
        f"queue timeout {timeout}s is not shorter than the client's "
        f"{MIN_REQUEST_TIMEOUT_SECONDS}s deadline"
    )


def test_serialisation_and_int8_default_safely_in_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config files can be lost; the defaults must not be the dangerous ones.

    Asks for the *code* default: no dotenv, and the variable removed from the
    environment. Without that, a developer whose .env matches an INT8 local
    graph would see this test fail for a reason that has nothing to do with the
    default.
    """
    from app.config import Settings

    monkeypatch.delenv("GITAGUIDE_EMBEDDER_PRECISION", raising=False)
    settings = Settings(mock_mode=True, _env_file=None)
    # Serialisation defaults ON: the safe direction, since off risks an OOM.
    assert settings.pipeline_serialise is True
    # Precision defaults to fp32: the safe direction, because an fp32 encoder
    # against an fp32 index is correct, and switching is what needs deliberate
    # action plus a re-embed.
    assert settings.embedder_precision == "fp32"


# CORS
#
# The service is Blueprint managed, so a sync applies whatever render.yaml says.
# A `sync: false` entry with a value typed into the dashboard is exactly what a
# sync would clear, and the failure is every /api call from the UI refused by
# the browser while the server reports nothing wrong.
FRONTEND_ORIGIN = "https://gitaguide-livid.vercel.app"


def _cors_origins(render_yaml: str) -> list[str]:
    """The origins render.yaml pins, parsed the way the app would parse them."""
    import json

    from app.config import Settings

    # Not `_env_value`: that helper stops at the first double quote, and this
    # value is a JSON list wrapped in single quotes so its quotes survive YAML.
    match = re.search(r"GITAGUIDE_CORS_ORIGINS\s*\n\s*value:\s*(.+)", render_yaml)
    assert match is not None, (
        "render.yaml does not pin GITAGUIDE_CORS_ORIGINS. Left to the dashboard, a "
        "Blueprint sync clears it and the UI is refused by CORS."
    )
    raw = match.group(1).strip().strip("'\"")
    parsed = json.loads(raw) if raw.startswith("[") else raw
    return Settings(mock_mode=True, cors_origins=parsed).cors_origins


def test_render_pins_the_frontend_origin_for_cors(render_yaml: str) -> None:
    origins = _cors_origins(render_yaml)
    assert FRONTEND_ORIGIN in origins, f"the deployed UI origin is missing from {origins}"


def test_cors_origins_are_exact_https_origins(render_yaml: str) -> None:
    """A trailing slash or a path is not an origin, and the browser will not match it."""
    for origin in _cors_origins(render_yaml):
        assert origin.startswith("https://"), f"{origin!r} is not https"
        assert not origin.endswith("/"), f"{origin!r} has a trailing slash"
        assert origin.count("/") == 2, f"{origin!r} carries a path; an origin has none"
        assert "*" not in origin, "a wildcard would open the API to any site"

