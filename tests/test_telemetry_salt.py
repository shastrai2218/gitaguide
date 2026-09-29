"""A prod deployment must not run on a telemetry salt that is not a secret.

The salt is the only thing that makes a telemetry session pseudonym
(``sha256(salt + session_id)``) a pseudonym. Three values make it public:
unset, the ``REPLACE_ME`` placeholder that ``.env.vm.example`` carries until
edited, and the code default, which this repository publishes. None of them
errors on its own, so the boot check does - in prod only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.gate_selftest import StartupConfigError, check_telemetry_salt

GOOD_SALT = "8f3c1e0b4d2a6c9e7b1f5a3d0c8e2b4f6a9d1c3e5b7f0a2d4c6e8b1f3a5d7c9e"


def _prod(salt: str) -> Settings:
    return Settings(mock_mode=True, environment="prod", telemetry_session_salt=salt)


@pytest.mark.parametrize(
    ("salt", "why"),
    [
        ("", "unset"),
        ("gitaguide-beta", "the public code default"),
        ("REPLACE_ME_WITH_A_LONG_RANDOM_STRING", "the template placeholder, unedited"),
        ("replace_me", "the placeholder in any case"),
        ("prefix-REPLACE_ME-suffix", "a placeholder someone typed around"),
    ],
)
def test_prod_refuses_a_salt_that_is_not_a_secret(salt: str, why: str) -> None:
    with pytest.raises(StartupConfigError) as failure:
        check_telemetry_salt(_prod(salt))
    assert "GITAGUIDE_TELEMETRY_SESSION_SALT" in str(failure.value), why


def test_prod_accepts_a_real_salt() -> None:
    check_telemetry_salt(_prod(GOOD_SALT))


@pytest.mark.parametrize("environment", ["dev", "test"])
@pytest.mark.parametrize("salt", ["", "gitaguide-beta", "REPLACE_ME_WITH_A_LONG_RANDOM_STRING"])
def test_outside_prod_any_salt_is_allowed(environment: str, salt: str) -> None:
    """A laptop, a test and the CI matrix all run on the default. That stays."""
    check_telemetry_salt(
        Settings(mock_mode=True, environment=environment, telemetry_session_salt=salt)
    )


def test_the_code_default_is_what_the_check_names() -> None:
    """If the default ever changes, the placeholder set must follow it."""
    from app.pipeline.gate_selftest import PLACEHOLDER_SALTS

    assert Settings(mock_mode=True, _env_file=None).telemetry_session_salt in PLACEHOLDER_SALTS


def test_the_app_will_not_start_in_prod_on_the_default_salt() -> None:
    """End to end: create_app + lifespan, not just the function."""
    app = create_app(Settings(mock_mode=True, environment="prod"))
    with pytest.raises(StartupConfigError), TestClient(app):
        pass


def test_the_app_starts_in_prod_with_a_real_salt() -> None:
    app = create_app(_prod(GOOD_SALT))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_the_template_still_ships_the_placeholder_the_check_catches() -> None:
    """The check and the template must agree on what 'unedited' looks like."""
    from pathlib import Path

    from app.pipeline.gate_selftest import PLACEHOLDER_SALT_MARKER

    template = (Path(__file__).resolve().parent.parent / ".env.vm.example").read_text(
        encoding="utf-8"
    )
    line = next(
        line for line in template.splitlines() if line.startswith("GITAGUIDE_TELEMETRY_SESSION_SALT=")
    )
    assert PLACEHOLDER_SALT_MARKER in line.split("=", 1)[1]
