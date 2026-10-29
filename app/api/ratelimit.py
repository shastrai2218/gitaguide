"""In-process rate limiting for the beta chat endpoint.

Called by: app.api.beta, before admission.

Why this exists, and why the numbers are what they are
-----------------------------------------------------
The NVIDIA NIM free tier allows roughly **40 requests per minute per model**,
and one normal GitaGuide turn spends **two** calls on the primary model: the
semantic intent classifier and the grounded generation. Malformed intent output
is re-asked once (measured ~4.5% of calls, see docs/kg_measurements.md section
11), so the realistic cost is ~2.05 calls per turn.

That puts the ceiling at roughly **19 turns per minute for the whole
deployment**, no matter how many testers there are.

This is the part worth being blunt about: **per-session and per-IP limits do not
bound that.** Twenty testers each allowed 5 messages a minute is 100 turns a
minute, five times over budget. Per-session limits stop one person monopolising
the service; only a **global** limiter protects the budget. All three are
implemented here, and the global one is the one that does the real work.

No external service and no Redis: a single uvicorn process serves the closed
beta, so the counters live in memory. That is a real limitation - it resets on
restart and would not survive a second worker - and it is recorded rather than
hidden. ``app/main.py`` refuses to pretend otherwise.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from app.config import Settings
from app.telemetry import Telemetry

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """Classic token bucket: ``capacity`` burst, refilled at ``rate_per_minute``.

    Chosen over a fixed window because a window lets a tester send the whole
    allowance at 59s and again at 61s - double the intended rate right at the
    boundary, which is exactly when several testers are likely to collide.
    """

    capacity: float
    rate_per_minute: float
    tokens: float = field(default=0.0)
    updated_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * (self.rate_per_minute / 60.0))
        self.updated_at = now

    def take(self, now: float | None = None) -> float:
        """Consume one token. Returns 0.0 if allowed, else seconds to wait."""
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        if self.rate_per_minute <= 0:
            return float("inf")
        deficit = 1.0 - self.tokens
        return math.ceil(deficit / (self.rate_per_minute / 60.0) * 10) / 10


@dataclass(frozen=True)
class RateLimitDecision:
    """Whether a request may proceed, and if not, which limit stopped it."""

    allowed: bool
    scope: str = ""
    retry_after: float = 0.0

    @property
    def retry_after_seconds(self) -> int:
        """``Retry-After`` is an integer number of seconds; never advertise 0."""
        return max(1, math.ceil(self.retry_after))


class RateLimiter:
    """Per-session, per-IP and global token buckets over one shared clock.

    Checked in cheapest-to-most-contended order, and **the global bucket is
    consumed last**: a request rejected by its session limit must not spend a
    token from the shared budget it never got to use.
    """

    def __init__(
        self,
        session_per_minute: float,
        session_burst: float,
        ip_per_minute: float,
        ip_burst: float,
        global_per_minute: float,
        global_burst: float,
        max_tracked: int = 5000,
    ) -> None:
        self.session_per_minute = session_per_minute
        self.session_burst = session_burst
        self.ip_per_minute = ip_per_minute
        self.ip_burst = ip_burst
        self.max_tracked = max_tracked

        self._sessions: dict[str, TokenBucket] = {}
        self._ips: dict[str, TokenBucket] = {}
        self._global = TokenBucket(capacity=global_burst, rate_per_minute=global_per_minute)
        self._lock = threading.Lock()

    def _bucket(
        self, store: dict[str, TokenBucket], key: str, capacity: float, rate: float
    ) -> TokenBucket:
        bucket = store.get(key)
        if bucket is None:
            if len(store) >= self.max_tracked:
                # Unbounded growth is a memory leak with a public endpoint in
                # front of it. Drop the least recently touched half.
                self._evict(store)
            bucket = TokenBucket(capacity=capacity, rate_per_minute=rate)
            store[key] = bucket
        return bucket

    @staticmethod
    def _evict(store: dict[str, TokenBucket]) -> None:
        ordered = sorted(store.items(), key=lambda item: item[1].updated_at)
        for key, _ in ordered[: len(ordered) // 2]:
            del store[key]

    def check(self, session_id: str | None, client_ip: str | None) -> RateLimitDecision:
        """Consume one request's worth of budget, or say how long to wait."""
        now = time.monotonic()
        with self._lock:
            if session_id:
                wait = self._bucket(
                    self._sessions, session_id, self.session_burst, self.session_per_minute
                ).take(now)
                if wait:
                    return RateLimitDecision(False, "session", wait)

            if client_ip:
                wait = self._bucket(self._ips, client_ip, self.ip_burst, self.ip_per_minute).take(
                    now
                )
                if wait:
                    return RateLimitDecision(False, "ip", wait)

            wait = self._global.take(now)
            if wait:
                return RateLimitDecision(False, "global", wait)

        return RateLimitDecision(True)

    def snapshot(self) -> dict[str, float]:
        """Current headroom, for ``/health`` and the telemetry counters."""
        with self._lock:
            self._global._refill(time.monotonic())
            return {
                "global_tokens": round(self._global.tokens, 2),
                "global_capacity": self._global.capacity,
                "global_per_minute": self._global.rate_per_minute,
                "tracked_sessions": float(len(self._sessions)),
                "tracked_ips": float(len(self._ips)),
            }


def client_ip_from(
    forwarded_for: str | None, peer: str | None, trusted_hops: int
) -> str | None:
    """The address to rate-limit on, given the proxy topology.

    ``X-Forwarded-For`` is written by clients as freely as by proxies, so it is
    only consulted when the deployment says how many trusted proxies stand in
    front of it. With ``trusted_hops=1`` this reads the **last** entry - the one
    the platform proxy appended - not the first, which is whatever the client
    chose to send. A client that forges ``X-Forwarded-For: 1.2.3.4`` therefore
    gets ``1.2.3.4, <their real address>`` and is still limited on their real
    address.

    With ``trusted_hops=0`` the header is ignored entirely and the socket peer
    is used, which is correct for local development and for any deployment not
    behind a proxy.
    """
    if trusted_hops <= 0 or not forwarded_for:
        return peer

    hops = [item.strip() for item in forwarded_for.split(",") if item.strip()]
    if not hops:
        return peer

    # Count in from the right: with N trusted proxies, the Nth-from-last entry
    # is the address the outermost trusted proxy observed. If the header is
    # shorter than claimed, the leftmost entry is the best available answer.
    index = max(0, len(hops) - trusted_hops)
    return hops[index] if index < len(hops) else hops[0]


#: Message shown to a rate-limited tester. Deliberately not an error tone: in a
#: closed beta the usual cause is other testers, not misuse.
RATE_LIMITED_MESSAGE = (
    "GitaGuide is thinking for someone else right now. "
    "Please wait a moment and send it again."
)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Construct the limiter from settings (kept out of the route module)."""
    return RateLimiter(
        session_per_minute=settings.rate_limit_session_per_minute,
        session_burst=settings.rate_limit_session_burst,
        ip_per_minute=settings.rate_limit_ip_per_minute,
        ip_burst=settings.rate_limit_ip_burst,
        global_per_minute=settings.rate_limit_global_per_minute,
        global_burst=settings.rate_limit_global_burst,
    )


def client_ip(request: Request) -> str | None:
    """The address to rate-limit this request on.

    Delegates the proxy reasoning to
    :func:`app.api.ratelimit.client_ip_from`, which reads ``X-Forwarded-For``
    only as far as the configured number of trusted proxies allows. Used solely
    as a rate-limit key; nothing security-relevant is decided from it.
    """
    settings = getattr(request.app.state, "settings", None)
    hops = settings.rate_limit_trusted_proxy_hops if settings is not None else 0
    return client_ip_from(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
        hops,
    )


def enforce_rate_limit(request: Request, session_id: str | None) -> None:
    """Raise 429 with ``Retry-After`` when a bucket is empty."""
    settings = getattr(request.app.state, "settings", None)
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None or (settings is not None and not settings.rate_limit_enabled):
        return

    decision = limiter.check(session_id, client_ip(request))
    if decision.allowed:
        return

    telemetry: Telemetry | None = getattr(request.app.state, "telemetry", None)
    if telemetry is not None:
        telemetry.record_rate_limited(decision.scope)

    wait = decision.retry_after_seconds
    logger.info(
        "rate limited: scope=%s retry_after=%ss session=%s",
        decision.scope,
        wait,
        session_id or "-",
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "message": RATE_LIMITED_MESSAGE,
            "retry_after_seconds": wait,
            "scope": decision.scope,
        },
        headers={"Retry-After": str(wait)},
    )
