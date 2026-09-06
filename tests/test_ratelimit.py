"""Rate limiting: the token bucket, and the 429 the endpoint returns.

The numbers exist to protect a ~40 req/min NIM budget that one turn spends two
calls of. The property that actually matters is the last test in this file: a
per-session limit does **not** bound aggregate spend, and only the global bucket
does. If that ever silently stops being true, the budget is unprotected while
every per-session limit still looks correct.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import RateLimiter, TokenBucket, client_ip_from
from app.config import Settings
from app.main import create_app


# --------------------------------------------------------------------------- #
# Token bucket
# --------------------------------------------------------------------------- #
def test_bucket_allows_a_burst_then_refuses() -> None:
    bucket = TokenBucket(capacity=3, rate_per_minute=5)
    assert [bucket.take(now=0.0) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.take(now=0.0) > 0.0


def test_bucket_refills_over_time() -> None:
    bucket = TokenBucket(capacity=3, rate_per_minute=60)  # one per second
    for _ in range(3):
        bucket.take(now=0.0)
    assert bucket.take(now=0.5) > 0.0, "half a token is not a token"
    assert bucket.take(now=1.0) == 0.0


def test_bucket_never_exceeds_capacity_after_a_long_idle() -> None:
    """A quiet hour must not buy an hour's worth of burst."""
    bucket = TokenBucket(capacity=3, rate_per_minute=60)
    bucket.take(now=0.0)
    assert [bucket.take(now=3600.0) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert bucket.take(now=3600.0) > 0.0


def test_retry_after_is_a_whole_number_of_seconds_and_never_zero() -> None:
    """``Retry-After: 0`` invites an immediate retry, which is the opposite."""
    limiter = RateLimiter(1, 1, 100, 100, 100, 100)
    limiter.check("s", "1.2.3.4")
    decision = limiter.check("s", "1.2.3.4")
    assert decision.allowed is False
    assert decision.retry_after_seconds >= 1
    assert isinstance(decision.retry_after_seconds, int)


# --------------------------------------------------------------------------- #
# Scope precedence
# --------------------------------------------------------------------------- #
def test_a_session_rejection_does_not_spend_global_budget() -> None:
    """One noisy tester must not drain the shared budget they never used."""
    limiter = RateLimiter(
        session_per_minute=1, session_burst=1,
        ip_per_minute=1000, ip_burst=1000,
        global_per_minute=1000, global_burst=10,
    )
    limiter.check("noisy", "1.2.3.4")
    for _ in range(20):
        assert limiter.check("noisy", "1.2.3.4").allowed is False

    assert limiter.snapshot()["global_tokens"] == pytest.approx(9.0, abs=0.5)


def test_the_global_bucket_stops_many_sessions_that_are_each_within_limits() -> None:
    """The point of the global limiter, stated as a test.

    Twenty sessions each politely under their own 5/min limit still exceed a
    16/min budget. Per-session limits cannot see that; the global one can.
    """
    limiter = RateLimiter(
        session_per_minute=5, session_burst=3,
        ip_per_minute=1000, ip_burst=1000,
        global_per_minute=16, global_burst=6,
    )
    allowed = sum(limiter.check(f"tester-{index}", f"10.0.0.{index}").allowed for index in range(20))
    assert allowed == 6, "the global burst is the ceiling, not 20 separate allowances"


def test_an_ip_shared_by_many_testers_is_not_the_binding_limit() -> None:
    """Testers behind one office NAT must not rate-limit each other."""
    limiter = RateLimiter(
        session_per_minute=5, session_burst=3,
        ip_per_minute=20, ip_burst=8,
        global_per_minute=1000, global_burst=1000,
    )
    allowed = sum(limiter.check(f"tester-{index}", "203.0.113.7").allowed for index in range(8))
    assert allowed == 8


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture
def limited_client() -> Iterator[TestClient]:
    """A tiny session budget so the 429 path is reachable in one test."""
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_session_per_minute=1,
            rate_limit_session_burst=1,
            rate_limit_ip_per_minute=1000,
            rate_limit_ip_burst=1000,
            rate_limit_global_per_minute=1000,
            rate_limit_global_burst=1000,
            consent_required=False,
        )
    )
    with TestClient(app) as client:
        yield client


def test_chat_returns_429_with_retry_after_and_a_friendly_message(
    limited_client: TestClient,
) -> None:
    body = {"session_id": "rl-session", "message": "I am overworked"}
    assert limited_client.post("/api/chat", json=body).status_code == 200

    response = limited_client.post("/api/chat", json=body)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1

    detail = response.json()["detail"]
    assert detail["scope"] == "session"
    assert detail["retry_after_seconds"] >= 1
    assert "wait" in detail["message"].lower()
    # Not an accusation: in a closed beta the usual cause is other testers.
    assert "abuse" not in detail["message"].lower()


def test_one_session_hitting_its_limit_does_not_block_another(
    limited_client: TestClient,
) -> None:
    first = {"session_id": "rl-a", "message": "I am overworked"}
    assert limited_client.post("/api/chat", json=first).status_code == 200
    assert limited_client.post("/api/chat", json=first).status_code == 429

    second = {"session_id": "rl-b", "message": "I am overworked"}
    assert limited_client.post("/api/chat", json=second).status_code == 200


# --------------------------------------------------------------------------- #
# Which address gets limited
# --------------------------------------------------------------------------- #
def test_without_a_declared_proxy_the_header_is_ignored() -> None:
    """The default must not believe a client-supplied header."""
    assert client_ip_from("1.2.3.4", "10.0.0.9", trusted_hops=0) == "10.0.0.9"
    assert client_ip_from(None, "10.0.0.9", trusted_hops=0) == "10.0.0.9"


def test_behind_one_proxy_the_appended_entry_is_used() -> None:
    """Render appends the address it saw; that is the one to limit on."""
    assert client_ip_from("203.0.113.7", "10.0.0.9", trusted_hops=1) == "203.0.113.7"


def test_a_forged_forwarded_for_cannot_choose_its_own_bucket() -> None:
    """The attack the hop count exists to stop.

    A client sending its own X-Forwarded-For gets that value *prepended* to
    what the proxy appends, so reading from the right still lands on their real
    address. Reading from the left - which this used to do - would have let
    anyone mint a fresh per-IP bucket per request.
    """
    forged = "1.2.3.4, 5.6.7.8, 203.0.113.7"
    assert client_ip_from(forged, "10.0.0.9", trusted_hops=1) == "203.0.113.7"


def test_a_missing_header_falls_back_to_the_socket_peer() -> None:
    assert client_ip_from(None, "10.0.0.9", trusted_hops=1) == "10.0.0.9"
    assert client_ip_from("   ", "10.0.0.9", trusted_hops=1) == "10.0.0.9"


def test_a_header_shorter_than_the_claimed_hops_uses_the_leftmost() -> None:
    """Misconfiguration degrades to the oldest entry, not to an index error."""
    assert client_ip_from("203.0.113.7", "10.0.0.9", trusted_hops=3) == "203.0.113.7"


def test_two_testers_behind_one_proxy_are_told_apart() -> None:
    """The point of reading the header at all."""
    first = client_ip_from("198.51.100.1", "10.0.0.9", trusted_hops=1)
    second = client_ip_from("198.51.100.2", "10.0.0.9", trusted_hops=1)
    assert first != second


# --------------------------------------------------------------------------- #
# Why production looked unlimited
# --------------------------------------------------------------------------- #
#: Production values, so this documents the real deployment rather than a toy.
PROD = {
    "session_per_minute": 5,
    "session_burst": 3,
    "ip_per_minute": 20,
    "ip_burst": 8,
    "global_per_minute": 16,
    "global_burst": 6,
}


def _sequential_trips(latency_seconds: float, requests: int) -> bool:
    """Send `requests` one at a time, `latency_seconds` apart. Did any get 429?"""
    limiter = RateLimiter(**PROD)
    for _ in range(requests):
        if not limiter.check("caller", "203.0.113.7").allowed:
            return True
        for bucket in (
            *limiter._sessions.values(),
            *limiter._ips.values(),
            limiter._global,
        ):
            bucket.updated_at -= latency_seconds
    return False


def test_a_sequential_caller_slower_than_the_refill_is_never_limited() -> None:
    """The behaviour that made production look broken when it was not.

    At 5 requests/minute the session bucket regains a token every 12s. Each
    sequential request costs one token and returns ``latency/12`` of one, so the
    net drain is ``1 - latency/12`` per request:

    * below 12s the bucket empties eventually, and a long enough run trips;
    * at or above 12s the drain is zero or negative and the bucket **never**
      empties, no matter how many requests are sent.

    Production turns take 5-25s and the observed p50 is 10.6s locally but higher
    on the free tier, which is why the same sequential burst tripped the limiter
    here and never tripped it in production. The limiter was correct throughout;
    the test method was not.

    Pinned so nobody "fixes" a working limiter in response to the same false
    alarm - and so the numbers stay honest if the session rate is ever changed.
    """
    refill_interval = 60 / PROD["session_per_minute"]
    many = 500  # far more than any burst the smoke test would send

    # Just below the interval the drain is tiny (0.008 tokens/request at 11.9s),
    # so it takes a long run - which is exactly why a 7-request burst proves
    # nothing either way.
    assert _sequential_trips(refill_interval - 0.1, requests=7) is False
    assert _sequential_trips(refill_interval - 0.1, requests=many) is True

    # At or above it, no run length is enough.
    assert _sequential_trips(refill_interval, requests=many) is False
    assert _sequential_trips(25.0, requests=many) is False, (
        "a slow production turn must never trip the limiter sequentially"
    )


def test_a_concurrent_burst_is_limited_at_the_burst_capacity() -> None:
    """How the limiter must actually be tested: no time passing between calls."""
    limiter = RateLimiter(**PROD)
    allowed = sum(limiter.check("caller", "203.0.113.7").allowed for _ in range(7))
    assert allowed == PROD["session_burst"], (
        "a genuine burst must be cut off at the session burst capacity"
    )
