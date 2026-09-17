"""The tester pack, and the one consent-version string it shares with the code.

Three documents in ``docs/tester-pack/`` are what a tester is sent and what the
app shows. Two of them (the Privacy Notice and the Disclaimer) are also served
as pages of the frontend from byte-for-byte copies. The version printed on them
is the version the frontend sends to ``/api/consent``, the version the server
records, and the version the production smoke test asserts. This module holds
all of those to one string, so a bump that misses one place fails here rather
than at the gate, where the symptom is "nobody can get past the first screen".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import CURRENT_CONSENT_VERSION

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "docs" / "tester-pack"
FRONTEND_CONTENT = ROOT / "frontend" / "src" / "content"

FRONTEND_URL = "https://gitaguide-livid.vercel.app"


# The documents exist and agree with their served copies
@pytest.mark.parametrize("name", ["ONBOARDING.md", "PRIVACY.md", "DISCLAIMER.md"])
def test_the_tester_pack_is_complete(name: str) -> None:
    path = PACK / name
    assert path.exists(), f"docs/tester-pack/{name} is missing"
    assert path.read_text(encoding="utf-8").strip(), f"docs/tester-pack/{name} is empty"


@pytest.mark.parametrize("name", ["PRIVACY.md", "DISCLAIMER.md"])
def test_the_served_copy_is_the_tester_pack_document(name: str) -> None:
    """The frontend renders these from frontend/src/content; they must not drift.

    Byte-for-byte, not "close enough": what a tester read on the page and what
    they were sent must be the same text, or the consent record describes a
    document nobody was shown.
    """
    source = (PACK / name).read_bytes()
    served = FRONTEND_CONTENT / name
    assert served.exists(), (
        f"frontend/src/content/{name} is missing; copy docs/tester-pack/{name} there"
    )
    assert served.read_bytes() == source, (
        f"frontend/src/content/{name} differs from docs/tester-pack/{name}. "
        "Edit the tester-pack document and copy it over; never edit the copy."
    )


# One version string, everywhere
@pytest.mark.parametrize("name", ["PRIVACY.md", "DISCLAIMER.md"])
def test_the_notices_print_the_version_the_server_requires(name: str) -> None:
    text = (PACK / name).read_text(encoding="utf-8")
    match = re.search(r"\*\*Version:\*\*\s*([\w.-]+)", text)
    assert match is not None, f"{name} has no '**Version:**' line"
    assert match.group(1) == CURRENT_CONSENT_VERSION, (
        f"{name} says version {match.group(1)!r} but the server requires "
        f"{CURRENT_CONSENT_VERSION!r}"
    )


def test_the_frontend_sends_the_version_the_server_requires() -> None:
    api_js = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    match = re.search(r"export const CONSENT_VERSION\s*=\s*'([^']+)'", api_js)
    assert match is not None, "frontend/src/api.js does not export CONSENT_VERSION"
    assert match.group(1) == CURRENT_CONSENT_VERSION


def test_the_browser_flag_is_keyed_by_version() -> None:
    """A browser that accepted an older notice must see the gate at once."""
    session_js = (ROOT / "frontend" / "src" / "session.js").read_text(encoding="utf-8")
    match = re.search(r"const CONSENT_KEY\s*=\s*'([^']+)'", session_js)
    assert match is not None
    assert match.group(1).endswith(CURRENT_CONSENT_VERSION)


def test_render_sets_the_version_the_server_requires() -> None:
    render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
    match = re.search(r"GITAGUIDE_CONSENT_VERSION\s*\n\s*value:\s*\"?([\w.-]+)\"?", render_yaml)
    assert match is not None, "render.yaml does not set GITAGUIDE_CONSENT_VERSION"
    assert match.group(1) == CURRENT_CONSENT_VERSION, (
        f"render.yaml sets {match.group(1)!r}; the frontend sends "
        f"{CURRENT_CONSENT_VERSION!r}, so nobody could get past the gate"
    )


def test_the_production_smoke_asserts_the_same_version() -> None:
    from scripts import prod_smoke

    assert prod_smoke.CONSENT_VERSION == CURRENT_CONSENT_VERSION
    assert prod_smoke.SUPERSEDED_CONSENT_VERSION != CURRENT_CONSENT_VERSION


# Onboarding says where the app is and what the texts are
def test_onboarding_gives_testers_the_url() -> None:
    text = (PACK / "ONBOARDING.md").read_text(encoding="utf-8")
    assert FRONTEND_URL in text, "ONBOARDING.md does not tell testers where the app is"


def test_onboarding_warns_about_the_arthashastra_translation() -> None:
    """A century-old public-domain translation reads oddly; testers should expect that."""
    text = (PACK / "ONBOARDING.md").read_text(encoding="utf-8").lower()
    assert "arthashastra" in text
    assert "public-domain" in text or "public domain" in text
    assert "1915" in text or "century-old" in text or "century old" in text
