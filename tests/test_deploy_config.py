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
#: The production settings the API container is started with. It is the tracked
#: template of the (gitignored) ``.env.vm`` on the server, so it is where a
#: change to a deployed value has to show up in review.
PRODUCTION_ENV = ROOT / ".env.vm.example"
#: The compose file that runs the API and its TLS proxy on the VM.
VM_COMPOSE = ROOT / "docker-compose.vm.yml"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def production_env() -> str:
    return PRODUCTION_ENV.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def vm_compose() -> str:
    return VM_COMPOSE.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """The lines that are not comments, so prose cannot trip a structural check."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


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


def test_the_vm_compose_does_not_override_the_start_command(vm_compose: str) -> None:
    """A ``command:`` in the compose file would silently win over the Dockerfile CMD."""
    for line in _code_lines(vm_compose):
        assert not re.match(r"^\s+command\s*:", line), (
            f"docker-compose.vm.yml sets {line.strip()!r}, which overrides the "
            "Dockerfile CMD and therefore bypasses the single-worker guarantee "
            "asserted above"
        )


def test_the_vm_runs_a_single_api_container(vm_compose: str) -> None:
    """Scaling out multiplies the limits exactly as extra workers would."""
    code = "\n".join(_code_lines(vm_compose))
    for key in ("replicas", "scale"):
        assert not re.search(rf"^\s+{key}\s*:", code, re.MULTILINE), (
            f"docker-compose.vm.yml sets {key}; the in-process limiter cannot "
            "span containers any more than it can span workers"
        )


# Proxy hops
def test_production_declares_its_proxy_hop_count(production_env: str) -> None:
    """Behind Caddy there is exactly one proxy, and it has to be declared.

    Left at the default of 0, the per-IP limit keys on the proxy's own address
    for every request and stops being a per-IP limit at all.
    """
    hops = _env_value(production_env, "GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS")
    assert hops is not None, (
        ".env.vm.example does not set GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS; the "
        "per-IP limit would key on the proxy address"
    )
    assert hops == "1", (
        f"trusted proxy hops is {hops}. Caddy is the one proxy in front of the "
        "container. A larger number lets a client choose its own rate-limit "
        "bucket by forging earlier X-Forwarded-For entries."
    )


def test_the_default_is_to_trust_no_proxy_headers() -> None:
    """Fail safe: an undeclared deployment must not believe the header."""
    from app.config import Settings

    assert Settings(mock_mode=True).rate_limit_trusted_proxy_hops == 0


# Memory-critical settings
#
# These only work as a set. INT8 is required because the graph is embedded with
# the INT8 encoder (an fp32 query against it silently returns worse neighbours),
# and INT8 was originally also what made a 512 MB host fit. INT8 alone still
# leaves a single turn near the cap on a small instance, so concurrent turns must
# also be prevented. Shipping one without the other looks fine in every test and
# dies under the first burst.
def _env_value(env_text: str, key: str) -> str | None:
    """The value a dotenv file assigns to ``key``; commented-out lines are ignored."""
    for line in _code_lines(env_text):
        match = re.match(rf"^{re.escape(key)}=(.*)$", line.strip())
        if match:
            return match.group(1).strip().strip("'\"")
    return None


def test_production_uses_the_int8_encoder(production_env: str) -> None:
    """The graph in Aura is embedded with INT8; the serving encoder must match."""
    assert _env_value(production_env, "GITAGUIDE_EMBEDDER_PRECISION") == "int8", (
        "production must set GITAGUIDE_EMBEDDER_PRECISION=int8: the stored vectors "
        "were written by the INT8 encoder, and querying them with fp32 does not "
        "error, it quietly returns worse neighbours"
    )


def test_int8_in_production_requires_serialised_turns(production_env: str) -> None:
    """The pairing this test exists for.

    INT8 buys headroom for *one* turn (429 MiB peak). Two concurrent turns
    exceed a small host, and the kernel's answer is SIGKILL - no traceback, a
    bare 502 and a restart. So if the deployment is on INT8 because memory is
    tight, it must also serialise; the two are one decision.
    """
    if _env_value(production_env, "GITAGUIDE_EMBEDDER_PRECISION") != "int8":
        pytest.skip("not an INT8 deployment; the pairing does not apply")

    assert _env_value(production_env, "GITAGUIDE_PIPELINE_SERIALISE") == "true", (
        "GITAGUIDE_EMBEDDER_PRECISION=int8 says this deployment is memory-bound, "
        "but GITAGUIDE_PIPELINE_SERIALISE is not true. INT8 leaves room for one "
        "turn, not two. Set both or neither."
    )


def test_production_pins_one_onnx_thread(production_env: str) -> None:
    """A second intra-op thread only costs memory on the small production hosts."""
    assert _env_value(production_env, "GITAGUIDE_ONNX_INTRA_OP_THREADS") == "1"


def test_the_queue_timeout_is_shorter_than_the_client_deadline(
    production_env: str,
) -> None:
    """A queued turn must be shed before the browser gives up on it.

    Otherwise the tester sees the frontend's timeout error for a request the
    server was still willing to serve, which is the confusing half of both
    failure modes at once.
    """
    timeout = _env_value(production_env, "GITAGUIDE_PIPELINE_QUEUE_TIMEOUT_SECONDS")
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
# A wrong or missing origin means every /api call from the UI is refused by the
# browser while the server reports nothing wrong, so the origin is pinned in the
# tracked template and held here.
FRONTEND_ORIGIN = "https://gitaguide-neon.vercel.app"


def _cors_origins_raw(production_env: str) -> str:
    raw = _env_value(production_env, "GITAGUIDE_CORS_ORIGINS")
    assert raw is not None, (
        ".env.vm.example does not pin GITAGUIDE_CORS_ORIGINS; without it the "
        "UI is refused by CORS"
    )
    return raw


def _cors_origins(production_env: str) -> list[str]:
    """The origins the template pins, parsed the way the app would parse them."""
    import json

    from app.config import Settings

    raw = _cors_origins_raw(production_env)
    parsed = json.loads(raw) if raw.startswith("[") else raw
    return Settings(mock_mode=True, cors_origins=parsed).cors_origins


def test_production_pins_the_frontend_origin_for_cors(production_env: str) -> None:
    origins = _cors_origins(production_env)
    assert FRONTEND_ORIGIN in origins, f"the deployed UI origin is missing from {origins}"


def test_cors_origins_are_a_json_list(production_env: str) -> None:
    """pydantic-settings parses a list field as JSON first, so ``a,b`` never boots.

    Found deploying to EC2 on 19 Sep 2026: a comma-separated value made the API
    exit at startup with a JSON decode error naming ``cors_origins``.
    """
    import json

    from app.config import Settings

    raw = _cors_origins_raw(production_env)
    assert raw.startswith("["), f"CORS origins must be a JSON list, got {raw!r}"
    # Exercise the real loading path, not just the parse: this is what failed.
    import os

    previous = os.environ.get("GITAGUIDE_CORS_ORIGINS")
    os.environ["GITAGUIDE_CORS_ORIGINS"] = raw
    try:
        assert Settings(mock_mode=True, _env_file=None).cors_origins == json.loads(raw)
    finally:
        if previous is None:
            os.environ.pop("GITAGUIDE_CORS_ORIGINS", None)
        else:
            os.environ["GITAGUIDE_CORS_ORIGINS"] = previous


def test_cors_origins_are_exact_https_origins(production_env: str) -> None:
    """A trailing slash or a path is not an origin, and the browser will not match it."""
    for origin in _cors_origins(production_env):
        assert origin.startswith("https://"), f"{origin!r} is not https"
        assert not origin.endswith("/"), f"{origin!r} has a trailing slash"
        assert origin.count("/") == 2, f"{origin!r} carries a path; an origin has none"
        assert "*" not in origin, "a wildcard would open the API to any site"


# The database connection string
#
# Found deploying to EC2 on 19 Sep 2026. The Supabase *direct* host is IPv6-only
# and unreachable from an EC2 instance; the *transaction* pooler (port 6543)
# fails this app's SQLAlchemy + asyncpg engine, which uses prepared statements,
# under concurrent queries; and asyncpg rejects ``?sslmode=``. The session
# pooler is the one that works, and the template must not steer anyone off it.
def test_the_database_dsn_template_uses_the_asyncpg_session_pooler(
    production_env: str,
) -> None:
    dsn = _env_value(production_env, "GITAGUIDE_POSTGRES_DSN")
    assert dsn is not None, ".env.vm.example does not set GITAGUIDE_POSTGRES_DSN"
    assert dsn.startswith("postgresql+asyncpg://"), "the driver must be asyncpg"
    assert "sslmode" not in dsn, "asyncpg rejects ?sslmode=; TLS is negotiated itself"
    assert ".pooler.supabase.com:5432/" in dsn, (
        "use Supabase's session pooler on port 5432: the transaction pooler (6543) "
        "breaks prepared statements, and the direct host is IPv6-only"
    )



# The image must contain what the image reads
#
# On 16 Sep 2026 the Render image had no data/ directory. The Dockerfile copied
# app, corpus, kg, eval and scripts, and data/intent_lexicon.yaml had shipped
# three days earlier in a directory nobody had added to that list. Stage 1 of
# the router was disabled for every message on every turn; the service stayed
# up and answered correctly, so the only symptom was that "hi" cost a model
# call. A prod smoke test found it, not a test in here.
#
# The general form of that mistake is a runtime file the image does not carry,
# so this checks the general form rather than the one file.
SOURCE_PACKAGES = ("app", "corpus", "kg")

#: ``PROJECT_ROOT / "data" / ...`` -> the first path segment, which is the
#: top-level directory the image must carry. A false positive here (a match
#: inside a comment or a string) can only demand an extra COPY, never permit a
#: missing one, so the failure direction is the safe one.
_PROJECT_ROOT_PATH_RE = re.compile(r'PROJECT_ROOT\s*/\s*"([^"/]+)"')
#: ``COPY <src> <dst>``, ignoring multi-stage ``COPY --from=`` forms.
_COPY_RE = re.compile(r"^COPY\s+(?!--)(\S+)\s+\S+\s*$", re.MULTILINE)


def _runtime_roots() -> set[str]:
    """Top-level directories the shipped code reads at ``PROJECT_ROOT``."""
    found: set[str] = set()
    for package in SOURCE_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found.update(_PROJECT_ROOT_PATH_RE.findall(path.read_text(encoding="utf-8")))
    return found


def _copied_roots(dockerfile: str) -> set[str]:
    return {source.split("/")[0].lstrip("./") for source in _COPY_RE.findall(dockerfile)}


def test_the_image_copies_every_directory_the_code_reads(dockerfile: str) -> None:
    """Every ``PROJECT_ROOT``-relative read must have a COPY behind it.

    This is the test that would have caught the missing lexicon before a
    deployment did, and it keeps working for the next file someone puts in a
    directory the Dockerfile has never heard of.
    """
    required = _runtime_roots()
    copied = _copied_roots(dockerfile)
    assert required, "found no PROJECT_ROOT-relative reads; the regex has rotted"
    missing = sorted(required - copied)
    assert not missing, (
        f"the Dockerfile does not COPY {missing}, but the code reads it at "
        f"PROJECT_ROOT. The image would run and degrade silently. Copied: "
        f"{sorted(copied)}"
    )


def test_the_lexicon_directory_in_particular_is_copied(dockerfile: str) -> None:
    """Named explicitly, because this is the one that actually shipped broken."""
    assert "data" in _copied_roots(dockerfile)
    assert (ROOT / "data" / "intent_lexicon.yaml").exists()


def test_the_build_context_excludes_the_graph_export() -> None:
    """`COPY data ./data` must not drag 68 MB of Neo4j dump into the image.

    data/graph_export is untracked, so Render's build context never had it -
    but a local `docker build` would, and a 68 MB image change is not something
    anyone would connect to a one-line Dockerfile edit.
    """
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data/graph_export" in ignore


# The router variables are declared, not left to defaults
#
# They were documented in DEPLOY.md on 13 Sep and never added to the deployed
# configuration, so for three days the deployment ran on code defaults that
# happened to match. That is luck, not configuration, and it is invisible in the
# hosting dashboard.
ROUTER_ENV = {
    "GITAGUIDE_ROUTER_MODEL_ENABLED": "true",
    "GITAGUIDE_ROUTER_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "GITAGUIDE_ROUTER_TIMEOUT_SECONDS": "6",
    "GITAGUIDE_ROUTER_MAX_TOKENS": "160",
    "GITAGUIDE_QUERY_REWRITE": "on",
}


@pytest.mark.parametrize(("key", "expected"), sorted(ROUTER_ENV.items()))
def test_production_declares_the_router_variables(
    production_env: str, key: str, expected: str
) -> None:
    assert _env_value(production_env, key) == expected


def test_the_declared_router_values_match_the_code_defaults() -> None:
    """A blueprint value that silently disagrees with the default is a trap.

    Whichever one a reader trusts, the other is wrong, and nothing says which
    the deployment used.
    """
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert str(settings.router_model_enabled).lower() == ROUTER_ENV[
        "GITAGUIDE_ROUTER_MODEL_ENABLED"
    ]
    assert settings.router_model == ROUTER_ENV["GITAGUIDE_ROUTER_MODEL"]
    assert settings.router_timeout_seconds == float(
        ROUTER_ENV["GITAGUIDE_ROUTER_TIMEOUT_SECONDS"]
    )
    assert settings.router_max_tokens == int(ROUTER_ENV["GITAGUIDE_ROUTER_MAX_TOKENS"])
    assert settings.query_rewrite is True
