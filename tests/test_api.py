"""API-level tests via ``TestClient`` (the app runs its real lifespan)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """One app instance for the module; mock mode is forced by env in conftest."""
    app = create_app(Settings(mock_mode=True, environment="test"))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mock_mode"] is True
    assert body["components"]["retrieval_backend"] == "in-memory-hybrid"


def test_chat_returns_the_full_explanation_payload(client: TestClient) -> None:
    response = client.post(
        "/chat", json={"message": "I am anxious that I will fail my appraisal"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["answer"]
    assert body["session_id"] and body["message_id"]
    assert body["emotion"]["dominant"] == "nervousness"
    assert 0.0 <= body["emotion"]["intensity"] <= 1.0
    assert body["intent"]["intent"] in {
        "seek_guidance",
        "emotional_support",
        "professional_dilemma",
        "scripture_question",
        "crisis",
        "other",
    }
    assert body["retrieval"]["verses"], "the explanation panel needs verses"
    assert body["citations"]
    assert body["grounded"] is True
    assert set(body["why_this_verse"]) == set(body["citations"])
    assert body["stages_run"][2] == "safety_input"


def test_retrieved_verse_carries_scores_and_paths(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "I am grieving my father"})
    verses = response.json()["retrieval"]["verses"]
    top = verses[0]
    for field in ("vector_score", "fulltext_score", "graph_score", "intent_boost", "final_score"):
        assert field in top
    assert top["channels"]
    assert top["why"]


def test_session_continuity(client: TestClient) -> None:
    first = client.post("/chat", json={"message": "Should I take the new offer?"}).json()
    second = client.post(
        "/chat",
        json={"message": "It pays less but the work is better", "session_id": first["session_id"]},
    ).json()
    assert second["session_id"] == first["session_id"]
    assert second["message_id"] != first["message_id"]


def test_crisis_response_has_no_retrieval(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "I want to kill myself"})
    assert response.status_code == 200
    body = response.json()
    assert body["crisis"] is True
    assert body["retrieval"] is None
    assert body["citations"] == []
    assert "14416" in body["answer"]
    assert body["safety"]["blocked"] is True
    assert body["stages_run"] == ["emotion", "intent", "safety_input"]


def test_explain_endpoint(client: TestClient) -> None:
    chat = client.post("/chat", json={"message": "My team ignores my suggestions"}).json()
    response = client.get(f"/explain/{chat['message_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["message_id"] == chat["message_id"]
    assert body["role"] == "assistant"
    assert body["retrieval"]["results"]
    assert body["generation"]["citations"] == chat["citations"]


def test_explain_unknown_message_is_404(client: TestClient) -> None:
    assert client.get("/explain/does-not-exist").status_code == 404


def test_safety_log_endpoint(client: TestClient) -> None:
    client.post("/chat", json={"message": "I have been cutting myself"})
    response = client.get("/safety/logs", params={"limit": 10})
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert rows[-1]["blocked"] is True
    assert rows[-1]["categories"]


@pytest.mark.parametrize("payload", [{}, {"message": ""}, {"message": "x" * 5000}])
def test_invalid_chat_payloads_are_422(client: TestClient, payload: dict) -> None:
    assert client.post("/chat", json=payload).status_code == 422


def test_openapi_documents_the_three_core_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/chat" in paths
    assert "/health" in paths
    assert "/explain/{message_id}" in paths
