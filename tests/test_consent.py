"""Consent recording, and the server-side refusal to serve without it.

A localStorage flag is a convenience for the tester, not a record: it does not
survive a cleared browser, it says nothing about *which* version of the notice
was shown, and it cannot be produced later as evidence. So consent is stored on
the session row and the chat endpoint refuses a session that has none.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.beta import CONSENT_REQUIRED_CODE
from app.config import Settings
from app.main import create_app

CHAT = {"session_id": "consent-session", "message": "I am overworked"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Consent enforced, rate limiting out of the way."""
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            consent_required=True,
            consent_version="v1",
            rate_limit_enabled=False,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def test_chat_without_consent_is_refused_with_409(client: TestClient) -> None:
    response = client.post("/api/chat", json=CHAT)
    assert response.status_code == 409

    detail = response.json()["detail"]
    assert detail["code"] == CONSENT_REQUIRED_CODE
    assert detail["required_version"] == "v1"
    assert "accept" in detail["message"].lower()


def test_consent_then_chat_succeeds(client: TestClient) -> None:
    consent = client.post(
        "/api/consent", json={"session_id": CHAT["session_id"], "version": "v1"}
    )
    assert consent.status_code == 200
    body = consent.json()
    assert body["session_id"] == CHAT["session_id"]
    assert body["consent_version"] == "v1"
    assert body["consent_at"]

    assert client.post("/api/chat", json=CHAT).status_code == 200


def test_consent_is_per_session_not_global(client: TestClient) -> None:
    """One tester consenting must not admit everyone else."""
    client.post("/api/consent", json={"session_id": "consent-a", "version": "v1"})
    assert client.post(
        "/api/chat", json={"session_id": "consent-a", "message": "hello"}
    ).status_code == 200
    assert client.post(
        "/api/chat", json={"session_id": "consent-b", "message": "hello"}
    ).status_code == 409


def test_an_outdated_consent_version_is_rejected(client: TestClient) -> None:
    """Bumping the notice must re-ask, not silently accept the old agreement."""
    response = client.post(
        "/api/consent", json={"session_id": "consent-old", "version": "v0"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["required_version"] == "v1"

    assert client.post(
        "/api/chat", json={"session_id": "consent-old", "message": "hello"}
    ).status_code == 409


def test_consent_survives_and_is_readable_on_the_session(client: TestClient) -> None:
    """It is a record, so it must still be there afterwards."""
    client.post("/api/consent", json={"session_id": "consent-read", "version": "v1"})
    orchestrator = client.app.state.orchestrator
    record = client.portal.call(orchestrator.repository.get_session, "consent-read")
    assert record is not None
    assert record.consent_version == "v1"
    assert record.consent_at is not None


def test_consent_can_be_disabled_only_deliberately() -> None:
    """The escape hatch exists for tests; prove it is off by default."""
    assert Settings(mock_mode=True).consent_required is True
