"""Per-turn feedback: thumbs up/down with an optional short comment.

The beta exists to find out whether the answers are any good, and the only
instrument for that is the tester's own verdict. So this endpoint has to be
hard to get wrong: one unambiguous verdict per turn, and a refusal to attach a
verdict to something that was never served.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def _one_turn(client: TestClient, session: str = "fb-session") -> str:
    body = client.post(
        "/api/chat", json={"session_id": session, "message": "I am overworked"}
    ).json()
    assert body["message_id"], "the UI cannot attach feedback without a message id"
    return body["message_id"]


def test_a_thumbs_up_is_recorded(client: TestClient) -> None:
    message_id = _one_turn(client)
    response = client.post(
        "/api/feedback", json={"message_id": message_id, "rating": "up"}
    )
    assert response.status_code == 200
    assert response.json() == {"message_id": message_id, "rating": "up", "comment": ""}

    stored = client.portal.call(
        client.app.state.orchestrator.repository.get_feedback, message_id
    )
    assert stored is not None and stored.rating == "up"


def test_a_comment_is_stored_with_the_rating(client: TestClient) -> None:
    message_id = _one_turn(client, "fb-comment")
    response = client.post(
        "/api/feedback",
        json={
            "message_id": message_id,
            "rating": "down",
            "comment": "  the verse did not fit what I asked  ",
        },
    )
    assert response.status_code == 200
    assert response.json()["comment"] == "the verse did not fit what I asked"


def test_changing_your_mind_replaces_the_verdict(client: TestClient) -> None:
    """One turn, one verdict - otherwise counting up-votes is guesswork."""
    message_id = _one_turn(client, "fb-change")
    client.post("/api/feedback", json={"message_id": message_id, "rating": "up"})
    client.post(
        "/api/feedback",
        json={"message_id": message_id, "rating": "down", "comment": "on reflection, no"},
    )

    stored = client.portal.call(
        client.app.state.orchestrator.repository.get_feedback, message_id
    )
    assert stored is not None
    assert stored.rating == "down"
    assert stored.comment == "on reflection, no"


def test_feedback_on_an_unknown_message_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/feedback", json={"message_id": "no-such-message", "rating": "up"}
    )
    assert response.status_code == 404


def test_feedback_on_the_users_own_message_is_refused(client: TestClient) -> None:
    """Only assistant turns can be rated; anything else is a caller bug."""
    session = "fb-user-msg"
    _one_turn(client, session)
    history = client.get(f"/api/sessions/{session}/messages").json()["messages"]
    user_message = next(item for item in history if item["role"] == "user")

    response = client.post(
        "/api/feedback", json={"message_id": user_message["id"], "rating": "up"}
    )
    assert response.status_code == 404


def test_an_invalid_rating_is_rejected(client: TestClient) -> None:
    message_id = _one_turn(client, "fb-bad-rating")
    response = client.post(
        "/api/feedback", json={"message_id": message_id, "rating": "sideways"}
    )
    assert response.status_code == 422


def test_an_overlong_comment_is_rejected(client: TestClient) -> None:
    """The cap also discourages pasting personal detail into a free-text box."""
    message_id = _one_turn(client, "fb-long")
    response = client.post(
        "/api/feedback",
        json={"message_id": message_id, "rating": "up", "comment": "x" * 501},
    )
    assert response.status_code == 422
