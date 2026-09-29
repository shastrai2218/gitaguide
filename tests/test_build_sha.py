"""The deployed commit is knowable: stamped at build time, reported by /health.

Until 30 Sep 2026 nothing recorded what production was running. ``/health``
said ``version: "0.1.0"`` for every commit, the deploy was ``git pull`` over
SSH, and every status report had to write UNKNOWN. The chain that fixes it has
four links - the shell sets ``GIT_SHA``, compose passes it as a build arg, the
Dockerfile turns it into ``GITAGUIDE_BUILD_SHA``, and ``/health`` reports it -
and each link is asserted here, because a break in any one of them degrades
silently to ``"unknown"``.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
VM_COMPOSE = (ROOT / "docker-compose.vm.yml").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "DEPLOY_AWS.md").read_text(encoding="utf-8")
SMOKE = (ROOT / "scripts" / "prod_smoke.py").read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


# The setting and the endpoint
def test_health_reports_the_configured_build_sha() -> None:
    app = create_app(Settings(mock_mode=True, environment="test", build_sha="abc1234"))
    with TestClient(app) as client:
        assert client.get("/health").json()["build_sha"] == "abc1234"


def test_an_unstamped_process_says_unknown_rather_than_inventing_one() -> None:
    assert Settings(mock_mode=True, _env_file=None).build_sha == "unknown"
    app = create_app(Settings(mock_mode=True, environment="test"))
    with TestClient(app) as client:
        assert client.get("/health").json()["build_sha"] == "unknown"


def test_the_setting_is_read_from_the_environment_the_image_sets(
    monkeypatch,
) -> None:
    """``ENV GITAGUIDE_BUILD_SHA`` in the Dockerfile is what the setting reads."""
    monkeypatch.setenv("GITAGUIDE_BUILD_SHA", "deadbee")
    assert Settings(mock_mode=True, _env_file=None).build_sha == "deadbee"


# The image
def test_the_dockerfile_declares_the_build_arg_and_exports_it() -> None:
    code = "\n".join(_code_lines(DOCKERFILE))
    assert re.search(r"^ARG\s+GIT_SHA=unknown\s*$", code, re.MULTILINE), (
        "the Dockerfile must declare `ARG GIT_SHA=unknown` so an unstamped "
        "build is honest rather than failing"
    )
    assert re.search(r"^ENV\s+GITAGUIDE_BUILD_SHA=\$\{?GIT_SHA\}?\s*$", code, re.MULTILINE), (
        "the Dockerfile must export the build arg as GITAGUIDE_BUILD_SHA, the "
        "name app.config reads"
    )


# The compose file
def test_the_vm_compose_passes_git_sha_as_a_build_arg() -> None:
    code = "\n".join(_code_lines(VM_COMPOSE))
    assert re.search(r"^\s+GIT_SHA:\s*\"\$\{GIT_SHA:-unknown\}\"", code, re.MULTILINE), (
        "docker-compose.vm.yml must pass GIT_SHA under build.args, defaulting "
        "to unknown so `docker compose config` never errors on a bare shell"
    )


# The runbook and the smoke test
def test_the_runbook_sets_git_sha_when_it_builds() -> None:
    """Both places the runbook shows a build: the first one and the dc helper."""
    builds = [
        line
        for line in RUNBOOK.splitlines()
        if ("docker compose" in line and "--build" in line) or line.lstrip().startswith("dc()")
    ]
    assert builds, "the runbook no longer shows how to build; update this test"
    for line in builds:
        assert "GIT_SHA=$(git rev-parse --short HEAD)" in line, (
            f"a build in docs/DEPLOY_AWS.md does not set GIT_SHA: {line.strip()!r}"
        )


def test_the_smoke_test_refuses_an_unknown_build_sha() -> None:
    assert 'sha != "unknown"' in SMOKE and "build sha is recorded" in SMOKE
