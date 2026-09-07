"""Consent recording, and the server-side refusal to serve without it.

A localStorage flag is a convenience for the tester, not a record: it does not
survive a cleared browser, it says nothing about *which* version of the notice
was shown, and it cannot be produced later as evidence. So consent is stored on
the session row and the chat endpoint refuses a session that has none.

The version matters as much as the fact. ``v1`` was the pre-launch placeholder;
``beta-1`` is the notice the tester pack actually carries. A session that agreed
to the placeholder agreed to nothing a tester was shown, so it is asked again.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.beta import CONSENT_REQUIRED_CODE
from app.config import CURRENT_CONSENT_VERSION, Settings
from app.main import create_app

CHAT = {"session_id": "consent-session", "message": "I am overworked"}
CURRENT = CURRENT_CONSENT_VERSION
SUPERSEDED = "v1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Consent enforced at the code-default version, rate limiting out of the way."""
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            consent_required=True,
            rate_limit_enabled=False,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def test_the_current_notice_version_is_beta_1() -> None:
    """The string the tester pack prints, the frontend sends and the server records."""
    assert CURRENT == "beta-1"
    assert Settings(mock_mode=True, _env_file=None).consent_version == "beta-1"


def test_chat_without_consent_is_refused_with_409(client: TestClient) -> None:
    response = client.post("/api/chat", json=CHAT)
    assert response.status_code == 409

    detail = response.json()["detail"]
    assert detail["code"] == CONSENT_REQUIRED_CODE
    assert detail["required_version"] == CURRENT
    assert "accept" in detail["message"].lower()


def test_consent_then_chat_succeeds(client: TestClient) -> None:
    consent = client.post(
        "/api/consent", json={"session_id": CHAT["session_id"], "version": CURRENT}
    )
    assert consent.status_code == 200
    body = consent.json()
    assert body["session_id"] == CHAT["session_id"]
    assert body["consent_version"] == CURRENT
    assert body["consent_at"]

    assert client.post("/api/chat", json=CHAT).status_code == 200


def test_consent_is_per_session_not_global(client: TestClient) -> None:
    """One tester consenting must not admit everyone else."""
    client.post("/api/consent", json={"session_id": "consent-a", "version": CURRENT})
    assert client.post(
        "/api/chat", json={"session_id": "consent-a", "message": "hello"}
    ).status_code == 200
    assert client.post(
        "/api/chat", json={"session_id": "consent-b", "message": "hello"}
    ).status_code == 409


def test_the_superseded_placeholder_version_is_rejected(client: TestClient) -> None:
    """Bumping the notice must re-ask, not silently accept the old agreement.

    ``v1`` is what every session before the tester pack existed agreed to. It
    must be refused with 400, the response must name the version now required,
    and the session must still be refused at ``/api/chat``.
    """
    response = client.post(
        "/api/consent", json={"session_id": "consent-old", "version": SUPERSEDED}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["required_version"] == CURRENT

    assert client.post(
        "/api/chat", json={"session_id": "consent-old", "message": "hello"}
    ).status_code == 409


def test_a_session_recorded_at_the_old_version_is_asked_again(client: TestClient) -> None:
    """The row exists and says v1; that is not consent to beta-1."""
    orchestrator = client.app.state.orchestrator
    client.portal.call(orchestrator.repository.record_consent, "consent-stale", SUPERSEDED)

    response = client.post("/api/chat", json={"session_id": "consent-stale", "message": "hello"})
    assert response.status_code == 409
    assert response.json()["detail"]["required_version"] == CURRENT

    # Accepting the current notice repairs it.
    assert client.post(
        "/api/consent", json={"session_id": "consent-stale", "version": CURRENT}
    ).status_code == 200
    assert client.post(
        "/api/chat", json={"session_id": "consent-stale", "message": "hello"}
    ).status_code == 200


def test_consent_survives_and_is_readable_on_the_session(client: TestClient) -> None:
    """It is a record, so it must still be there afterwards."""
    client.post("/api/consent", json={"session_id": "consent-read", "version": CURRENT})
    orchestrator = client.app.state.orchestrator
    record = client.portal.call(orchestrator.repository.get_session, "consent-read")
    assert record is not None
    assert record.consent_version == CURRENT
    assert record.consent_at is not None


def test_consent_can_be_disabled_only_deliberately() -> None:
    """The escape hatch exists for tests; prove it is off by default."""
    assert Settings(mock_mode=True).consent_required is True
