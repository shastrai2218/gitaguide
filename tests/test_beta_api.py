"""Tests for the beta UI adapter (``/api/chat``, ``/api/sessions/*/messages``).

The adapter is a projection, so these tests check exactly two things: that the
envelope carries what the UI needs, and that a blocked turn stays blocked all
the way to the wire - no citations, no retrieval, and the crisis text intact.

Mock mode is forced by ``conftest`` before collection, so nothing here reaches
Neo4j, Postgres, or NVIDIA NIM.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    app = create_app(Settings(mock_mode=True, environment="test"))
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_beta_chat_returns_the_ui_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        json={
            "session_id": "beta-happy",
            "message": "My manager took credit for my work and I am furious",
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["reply"]
    assert body["blocked"] is False

    assert isinstance(body["emotion"]["label"], str) and body["emotion"]["label"]
    assert 0.0 <= body["emotion"]["score"] <= 1.0
    assert isinstance(body["intent"]["label"], str) and body["intent"]["label"]
    assert 0.0 <= body["intent"]["crisis_score"] <= 1.0

    assert body["citations"], "a normal turn must show the verses it drew on"
    for citation in body["citations"]:
        assert citation["source"]
        assert citation["ref"]
        assert citation["text"]

    assert body["meta"]["provider"]
    assert body["meta"]["latency_ms"] >= 0.0


def test_beta_chat_cited_verses_come_from_retrieval(client: TestClient) -> None:
    """Every citation on the wire must correspond to a verse actually retrieved.

    The research surface is the source of truth here: whatever ``/chat`` reports
    as its citation ids must be exactly what the adapter flattens.
    """
    message = "I keep second-guessing every decision I make at work"
    full = client.post("/chat", json={"session_id": "beta-parity", "message": message})
    beta = client.post(
        "/api/chat", json={"session_id": "beta-parity-2", "message": message}
    )

    assert full.status_code == beta.status_code == 200
    retrieved = {verse["citation"] for verse in full.json()["retrieval"]["verses"]}
    for citation in beta.json()["citations"]:
        assert citation["ref"] in retrieved


def test_beta_chat_reports_memory_state(client: TestClient) -> None:
    """The memory block reflects the session, and grows across turns."""
    session = "beta-memory"
    first = client.post(
        "/api/chat", json={"session_id": session, "message": "I feel stuck in my role"}
    ).json()
    second = client.post(
        "/api/chat", json={"session_id": session, "message": "Should I move teams?"}
    ).json()

    # Turn one sees a session that does not exist yet: an honest zero.
    assert first["memory"]["turn_count"] == 0
    assert first["memory"]["recent_turns_injected"] == 0

    # Turn two sees the first exchange already persisted.
    assert second["memory"]["turn_count"] >= 1
    assert second["memory"]["recent_turns_injected"] >= 1
    assert second["memory"]["summary_included"] is False


# --------------------------------------------------------------------------- #
# Blocked turn
# --------------------------------------------------------------------------- #
def test_beta_chat_blocked_turn_shows_crisis_text_and_no_citations(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/chat",
        json={"session_id": "beta-crisis", "message": "I want to kill myself"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["blocked"] is True
    assert body["citations"] == [], "a blocked turn ran no retrieval; it cannot cite"
    assert "14416" in body["reply"], "the crisis text must reach the UI intact"


def test_beta_chat_blocked_turn_runs_no_retrieval_or_generation(
    client: TestClient,
) -> None:
    """Cross-check against the research surface: the gate short-circuits.

    The same message on ``/chat`` must show retrieval and generation absent, so
    ``blocked`` on the beta envelope means what the UI assumes it means.
    """
    message = "I want to kill myself"
    full = client.post("/chat", json={"session_id": "beta-crisis-2", "message": message})
    body = full.json()
    assert body["crisis"] is True
    assert body["retrieval"] is None
    assert body["stages_run"] == ["emotion", "intent", "safety_input"]


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def test_beta_session_messages_returns_persisted_turns(client: TestClient) -> None:
    session = "beta-history"
    client.post("/api/chat", json={"session_id": session, "message": "I am overworked"})

    response = client.get(f"/api/sessions/{session}/messages")
    assert response.status_code == 200
    body = response.json()

    assert body["session_id"] == session
    roles = [message["role"] for message in body["messages"]]
    assert "user" in roles and "assistant" in roles
    assert body["messages"][0]["content"] == "I am overworked"
    for message in body["messages"]:
        assert message["id"] and message["created_at"]


def test_beta_session_messages_unknown_session_is_empty(client: TestClient) -> None:
    response = client.get("/api/sessions/no-such-session/messages")
    assert response.status_code == 200
    assert response.json()["messages"] == []
