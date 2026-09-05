"""Telemetry: text-free per-turn events, and the counters that watch the risk.

Two things are being protected here.

The first is privacy: the consent notice tells testers their messages are
stored in the database, not scattered across log files. The no-text rule is
asserted by pushing a distinctive phrase through a real turn and grepping the
written line for it.

The second is the Day 3 residual risk. The mitigation in section 11 shipped with
a stated remaining window - a no-cue phrasing on a turn where the semantic
classifier fails to parse twice. These counters are how that window stays
observable in beta instead of being a number someone measured once.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.telemetry import RISK_COUNTERS, DailyCounters, Telemetry, hash_session

SECRET = "zebra-mango-particular-phrase"


@pytest.fixture
def telemetry_client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    log_path = tmp_path / "telemetry" / "events.jsonl"
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
            telemetry_enabled=True,
            telemetry_path=str(log_path),
            telemetry_session_salt="test-salt",
        )
    )
    with TestClient(app) as client:
        yield client, log_path


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #
def test_no_message_text_is_ever_written(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    client, log_path = telemetry_client
    client.post("/api/chat", json={"session_id": "tele-1", "message": SECRET})

    raw = log_path.read_text(encoding="utf-8")
    assert raw.strip(), "the turn should have produced an event"
    assert SECRET not in raw
    for word in SECRET.split("-"):
        assert word not in raw, f"{word!r} leaked into telemetry"


def test_the_session_id_is_hashed_not_logged(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    client, log_path = telemetry_client
    client.post("/api/chat", json={"session_id": "tele-secret-session", "message": "hello"})

    raw = log_path.read_text(encoding="utf-8")
    assert "tele-secret-session" not in raw
    assert _events(log_path)[0]["session"] == hash_session("tele-secret-session", "test-salt")


def test_the_same_session_hashes_consistently_and_others_differ() -> None:
    """Grouping a conversation must work; identifying the person must not."""
    assert hash_session("a", "salt") == hash_session("a", "salt")
    assert hash_session("a", "salt") != hash_session("b", "salt")
    assert hash_session("a", "salt") != hash_session("a", "other-salt")
    assert hash_session(None, "salt") == "anon"


# --------------------------------------------------------------------------- #
# Event content
# --------------------------------------------------------------------------- #
def test_an_event_carries_what_an_investigation_needs(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    client, log_path = telemetry_client
    client.post("/api/chat", json={"session_id": "tele-2", "message": "I am overworked"})

    event = _events(log_path)[0]
    for field in (
        "ts", "session", "message_id", "blocked", "gate_layers", "intent",
        "semantic_status", "semantic_retried", "model", "stages", "total_ms",
    ):
        assert field in event, f"telemetry is missing {field}"
    assert event["stages"], "per-stage latency is the point of the Day 3 work"


def test_a_blocked_turn_is_recorded_as_blocked(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    client, log_path = telemetry_client
    client.post(
        "/api/chat", json={"session_id": "tele-3", "message": "I want to kill myself"}
    )

    event = _events(log_path)[0]
    assert event["blocked"] is True
    assert "rule" in event["gate_layers"]
    # The crisis text itself must not be in the log either.
    assert "14416" not in log_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Daily counters
# --------------------------------------------------------------------------- #
def test_risk_counters_are_always_present_even_at_zero() -> None:
    """A missing counter must never be read as "that never happens"."""
    counters = DailyCounters()
    today = counters.today()
    for name in RISK_COUNTERS:
        assert today[name] == 0


def test_counters_tally_per_day_and_drop_old_days() -> None:
    counters = DailyCounters(keep_days=2)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        counters.increment("turns", day=day)
    assert "2026-09-01" not in counters.all_days()
    assert counters.for_day("2026-09-03")["turns"] == 1


def test_turns_and_blocks_are_counted(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    client, _ = telemetry_client
    client.post("/api/chat", json={"session_id": "tele-4", "message": "I am overworked"})
    client.post(
        "/api/chat", json={"session_id": "tele-5", "message": "I want to kill myself"}
    )

    today = client.app.state.telemetry.counters.today()
    assert today["turns"] == 2
    assert today["blocked_turns"] == 1


def test_health_surfaces_todays_counters(
    telemetry_client: tuple[TestClient, Path],
) -> None:
    """Watchable without opening a file, which is what makes it get watched."""
    client, _ = telemetry_client
    client.post("/api/chat", json={"session_id": "tele-6", "message": "I am overworked"})

    components = client.get("/health").json()["components"]
    assert components["telemetry_today"]["turns"] == 1
    assert set(RISK_COUNTERS) <= set(components["telemetry_today"])
    assert "rate_limit" in components


def test_rate_limit_rejections_are_counted(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            consent_required=False,
            rate_limit_session_per_minute=1,
            rate_limit_session_burst=1,
            telemetry_enabled=True,
            telemetry_path=str(tmp_path / "events.jsonl"),
        )
    )
    with TestClient(app) as client:
        body = {"session_id": "tele-rl", "message": "I am overworked"}
        client.post("/api/chat", json=body)
        assert client.post("/api/chat", json=body).status_code == 429

        today = client.app.state.telemetry.counters.today()
        assert today["rate_limited"] == 1
        assert today["rate_limited_session"] == 1


def test_telemetry_can_be_disabled_and_then_writes_nothing(tmp_path: Path) -> None:
    log_path = tmp_path / "off.jsonl"
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
            telemetry_enabled=False,
            telemetry_path=str(log_path),
        )
    )
    with TestClient(app) as client:
        client.post("/api/chat", json={"session_id": "tele-off", "message": "hello"})
    assert not log_path.exists()


def test_a_broken_log_path_does_not_break_a_turn(tmp_path: Path) -> None:
    """Telemetry is instrumentation; it must never take the service down."""
    unwritable = tmp_path / "file-not-a-dir"
    unwritable.write_text("blocking", encoding="utf-8")
    settings = Settings(
        mock_mode=True,
        environment="test",
        telemetry_enabled=True,
        telemetry_path=str(unwritable / "nested" / "events.jsonl"),
    )
    telemetry = Telemetry(settings)
    assert telemetry.record_turn(object()) is None  # bad result object, no raise
    telemetry.record_counter("upstream_retry")
