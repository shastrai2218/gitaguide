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
    """Adapter tests, with rate limiting off.

    The limiter is deliberately disabled here: this module sends more turns in a
    few seconds than the global budget allows in a minute, and it is testing the
    projection, not the limiter. Rate limiting has its own tests in
    ``tests/test_ratelimit.py``.
    """
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


# Happy path
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
    """The memory block comes from the pipeline and grows across turns.

    ``turn_count`` counts messages, and the user's own message is persisted
    before the memory stage runs, so exchange 1 already reports 1 and exchange 2
    reports 3 (user, assistant, user).
    """
    session = "beta-memory"
    first = client.post(
        "/api/chat", json={"session_id": session, "message": "I feel stuck in my role"}
    ).json()
    second = client.post(
        "/api/chat", json={"session_id": session, "message": "Should I move teams?"}
    ).json()

    assert first["memory"] == {
        "turn_count": 1,
        "summary_included": False,
        "recent_turns_injected": 1,
    }
    assert second["memory"] == {
        "turn_count": 3,
        "summary_included": False,
        "recent_turns_injected": 3,
    }


def test_beta_chat_memory_matches_the_research_surface(client: TestClient) -> None:
    """One source of truth: the adapter must not recompute memory itself.

    Both surfaces project the same ``GuidanceResult.memory``, so the same turn
    number in two parallel sessions must report identical memory blocks.
    """
    message = "I am unsure whether to accept the offer"
    beta = client.post(
        "/api/chat", json={"session_id": "beta-mem-a", "message": message}
    ).json()
    full = client.post(
        "/chat", json={"session_id": "beta-mem-b", "message": message}
    ).json()

    assert beta["memory"]["turn_count"] == full["memory"]["turn_count"]
    assert beta["memory"]["summary_included"] == full["memory"]["summary_included"]
    assert (
        beta["memory"]["recent_turns_injected"]
        == full["memory"]["recent_turns_injected"]
    )


# Blocked turn
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


# History
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


# The citation surface (added 9 Sep 2026)
#
# The reply stopped carrying the verse text and the graph-path explanation, so
# the citation payload has to carry both - otherwise "shorter answers" would
# just mean "less evidence", which is the opposite of the point.
def test_each_citation_carries_its_id_label_and_full_text(client: TestClient) -> None:
    body = client.post(
        "/api/chat",
        json={"session_id": "cite-shape", "message": "I feel stuck between two jobs"},
    ).json()

    assert body["citations"], "a normal turn must cite something"
    for citation in body["citations"]:
        assert citation["id"], "the machine id must survive to the UI"
        assert citation["label"] == citation["id"].replace("-", " ", 1)
        assert citation["ref"]
        assert citation["text"], "the full verse must still be available to the reader"
        assert citation["source"] in {"bhagavad_gita", "arthashastra"}


def test_the_explanation_travels_with_the_citation_not_the_reply(
    client: TestClient,
) -> None:
    """The explainability claim, relocated rather than removed.

    The graph path used to be a "Why this verse" section in the answer body.
    It must now arrive per citation, and it must not be in the reply.
    """
    body = client.post(
        "/api/chat",
        json={"session_id": "cite-why", "message": "My team keeps missing deadlines"},
    ).json()

    assert body["citations"]
    assert all(citation["why"] for citation in body["citations"]), (
        "every cited verse must say why it was retrieved"
    )
    assert "why this verse" not in body["reply"].lower()


def test_gita_citations_link_to_the_authoritative_edition(client: TestClient) -> None:
    """Decision (b'): link BBT's edition, never store or transmit it.

    A Gita citation deep-links vedabase.io at the verse the id names. The
    Arthashastra has no canonical per-sutra URL and carries none.
    """
    seen_gita = False
    for message in (
        "I feel stuck between two jobs",
        "My team keeps missing deadlines",
        "I am anxious about my appraisal",
    ):
        body = client.post(
            "/api/chat", json={"session_id": f"cite-url-{hash(message)}", "message": message}
        ).json()
        for citation in body["citations"]:
            if citation["source"] == "bhagavad_gita":
                seen_gita = True
                chapter, verse = citation["id"].removeprefix("BG-").split(".")
                assert citation["url"] == (
                    f"https://vedabase.io/en/library/bg/{chapter}/{verse}"
                )
            else:
                assert citation["url"] == "", "the Arthashastra has no per-sutra URL"
    assert seen_gita, "the sample corpus should have produced at least one Gita citation"


def test_a_blocked_turn_still_carries_no_citations_at_all(client: TestClient) -> None:
    """The new fields must not become a way for a crisis turn to cite scripture."""
    body = client.post(
        "/api/chat",
        json={"session_id": "cite-blocked", "message": "I want to kill myself"},
    ).json()
    assert body["blocked"] is True
    assert body["citations"] == []


# Phase 1b - routing, as the wire sees it
def test_a_greeting_reaches_the_wire_with_no_citations_and_no_retrieval(
    client: TestClient,
) -> None:
    """The projection of a no-retrieval turn.

    ``_citations_for`` already returned nothing when ``retrieval is None`` - it
    was written that way for the crisis path, and the greeting path inherits
    it. That is worth an assertion rather than an assumption, because the
    inheritance is incidental: nothing in that function mentions greetings.
    """
    body = client.post(
        "/api/chat", json={"session_id": "beta-greeting", "message": "hi"}
    ).json()
    assert body["blocked"] is False
    assert body["reply"].strip()
    assert body["citations"] == []
    assert "retrieval" not in body["meta"]["stage_latency_ms"]
    assert "router" in body["meta"]["stage_latency_ms"]


def test_a_greeting_reply_carries_no_verse_reference_in_its_prose(
    client: TestClient,
) -> None:
    """A reference with no citation surface behind it is a dead link."""
    import re

    body = client.post(
        "/api/chat", json={"session_id": "beta-greeting-prose", "message": "namaste"}
    ).json()
    assert not re.search(r"\b(?:BG|AS)-\d", body["reply"]), body["reply"]


def test_a_real_question_still_retrieves_and_cites_over_the_wire(
    client: TestClient,
) -> None:
    """Routing must not have turned the product off."""
    body = client.post(
        "/api/chat",
        json={
            "session_id": "beta-guidance",
            "message": "My manager takes credit for my work and I am getting bitter about it.",
        },
    ).json()
    assert body["blocked"] is False
    assert body["citations"]
    assert "retrieval" in body["meta"]["stage_latency_ms"]
