"""Per-turn telemetry: one JSON line per turn, plus daily counters.

Called by: app.api.beta, app.pipeline.generation, app.pipeline.router.

Why this exists
---------------
Two of the numbers this project cares most about were measured **once**, by
hand, on one afternoon: the semantic classifier's ~4.5% malformed-output rate,
and the crisis-gate miss that followed from it (docs/kg_measurements.md sections
10-11). The mitigation shipped with a stated residual risk. A residual risk that
nobody can see is indistinguishable from one that has gone away.

So the four numbers that bound that risk are counted continuously, per day:

* ``semantic_retried``          - how often the malformed-output retry fired
* ``semantic_malformed_final``  - how often **both** attempts failed, which is
                                  the window in which the semantic safety layer
                                  is simply not there
* ``soft_cue_fallback_blocks``  - how often the conservative fallback caught a
                                  crisis-adjacent message in that window
* ``audit_only_near_miss``      - gate signals that did not block

Privacy
-------
**No message text, ever.** Not the user's, not the assistant's, not an excerpt,
not a matched pattern. The session id is hashed with a per-deployment salt, so
turns can be grouped into a conversation for analysis without the log naming
who was talking. :func:`build_event` is where that rule lives, and a test
asserts it by feeding a distinctive phrase through and grepping the output.

No external service: a rotating local file, read with ``tail`` and ``jq``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import threading
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

#: Counter names that answer "is the Day 3 residual risk still small?".
RISK_COUNTERS = (
    "semantic_retried",
    "semantic_malformed_final",
    "soft_cue_fallback_blocks",
    "audit_only_near_miss",
)

#: Counters that account for every turn admitted to the pipeline.
#:
#: On 9 Sep 2026 production showed 15 pipeline admissions against 2 completed
#: turns. The 13 missing ones could only be *inferred* - a 5xx or a client
#: disconnect - because a cancelled request increments the admission counter,
#: then skips both ``record_turn`` and the 500 handler: ``asyncio.CancelledError``
#: is a ``BaseException``, so ``except Exception`` never sees it.
#:
#: ``turns_cancelled`` closes that gap. With it, admitted == completed + shed +
#: cancelled + 5xx, and the residue is the 5xx count without needing the logs.
RELIABILITY_COUNTERS = (
    "turns_cancelled",
    "turns_shed",
    #: The conversational router asked its model and could not use the answer -
    #: timeout, transport error, malformed JSON or an unknown label. The turn is
    #: still answered, as guidance, so this is a quality signal rather than an
    #: error rate: while it is low the router is doing its job, and if it climbs
    #: the fix is ``router_model_enabled=false``, not a redeploy.
    "intent_router_fallback",
)

#: Counters for the output sanity guard (``generation.assess_answer``). A
#: production turn on 8 Sep 2026 served word salad from the fallback model with
#: every existing check green: grounded, cited, output gate clear. These count
#: how often the guard fires, how often one retry fixes it, and how often the
#: tester gets the overload reply instead. Always present, so "not shown" can
#: never be read as "not happening".
QUALITY_COUNTERS = (
    "degenerate_output_detected",
    "degenerate_output_recovered",
    "degenerate_output_shed",
)


def hash_session(session_id: str | None, salt: str) -> str:
    """Stable pseudonym for a session: groupable, not reversible to the id."""
    if not session_id:
        return "anon"
    digest = hashlib.sha256(f"{salt}:{session_id}".encode()).hexdigest()
    return digest[:16]


class DailyCounters:
    """Per-UTC-day tallies, kept in memory and emitted with every event.

    In-process, like the rate limiter: they reset on restart, which is fine
    because the log file is the durable record and these are the convenience
    view. ``/health`` serves today's numbers so a tester run can be checked
    without opening the file.
    """

    def __init__(self, keep_days: int = 14) -> None:
        self._days: dict[str, Counter[str]] = {}
        self._keep_days = keep_days
        self._lock = threading.Lock()

    def increment(self, name: str, day: str | None = None, amount: int = 1) -> None:
        key = day or date.today().isoformat()
        with self._lock:
            bucket = self._days.setdefault(key, Counter())
            bucket[name] += amount
            if len(self._days) > self._keep_days:
                for stale in sorted(self._days)[: len(self._days) - self._keep_days]:
                    del self._days[stale]

    def today(self) -> dict[str, int]:
        return self.for_day(date.today().isoformat())

    def for_day(self, day: str) -> dict[str, int]:
        with self._lock:
            bucket = self._days.get(day, Counter())
            counts = dict(bucket)
        # The risk and quality counters are always present, at zero, so "not
        # shown" can never be mistaken for "not happening".
        for name in (*RISK_COUNTERS, *QUALITY_COUNTERS, *RELIABILITY_COUNTERS):
            counts.setdefault(name, 0)
        return counts

    def all_days(self) -> dict[str, dict[str, int]]:
        with self._lock:
            days = sorted(self._days)
        return {day: self.for_day(day) for day in days}


def build_event(
    *,
    result: Any,
    salt: str,
    surface: str = "api",
) -> dict[str, Any]:
    """Project one :class:`GuidanceResult` onto a text-free telemetry event.

    Everything here is a label, a number, or a boolean. If a field could carry
    what somebody typed, it does not belong in this function.
    """
    intent = result.intent
    generation = result.generation
    safety = result.safety

    return {
        "ts": datetime.now(UTC).isoformat(),
        "surface": surface,
        "session": hash_session(result.session_id, salt),
        # An opaque id, useful for joining to the database when investigating a
        # specific turn. It reveals nothing on its own.
        "message_id": result.message_id,
        "blocked": bool(result.crisis),
        "gate_layers": list(safety.layers),
        "gate_categories": list(safety.categories),
        "audit_only": bool(safety.signals) and not safety.blocked,
        "emotion": result.emotion.dominant,
        "emotion_intensity": round(result.emotion.intensity, 4),
        "intent": intent.intent.value,
        "semantic_status": intent.semantic_status.value,
        "semantic_parse_status": intent.semantic_parse_status.value,
        "semantic_first_parse_status": intent.semantic_first_parse_status.value,
        "semantic_retried": bool(intent.semantic_retried),
        "semantic_crisis_score": round(intent.semantic_crisis_score, 4),
        "provider": generation.provider if generation else None,
        "model": generation.model if generation else None,
        "fallback_steps": generation.fallback_steps if generation else 0,
        "grounded": generation.grounded if generation else None,
        "citations": len(generation.citations) if generation else 0,
        # "", "retried" or "shed" - a label, never the text it judged.
        "quality_guard": generation.quality_guard if generation else "",
        "stages": dict(result.timings),
        "total_ms": round(result.total_latency_ms, 1),
        "memory_turn_count": result.memory.turn_count,
        "memory_summary_included": result.memory.summary_included,
    }


class Telemetry:
    """Writes events to a rotating JSONL file and keeps the daily tallies."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = settings.telemetry_enabled
        self.salt = settings.telemetry_session_salt
        self.counters = DailyCounters()
        self._writer: logging.Logger | None = None
        if self.enabled:
            self._writer = self._build_writer(settings)

    @staticmethod
    def _build_writer(settings: Settings) -> logging.Logger | None:
        path = Path(settings.telemetry_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=settings.telemetry_max_bytes,
                backupCount=settings.telemetry_backup_count,
                encoding="utf-8",
            )
        except OSError:
            # Telemetry must never take the service down with it.
            logger.exception("telemetry file unavailable at %s; events will not be written", path)
            return None

        handler.setFormatter(logging.Formatter("%(message)s"))
        writer = logging.getLogger("gitaguide.telemetry")
        writer.setLevel(logging.INFO)
        writer.propagate = False  # JSONL only; not mixed into the app log
        writer.handlers = [handler]
        return writer

    # ------------------------------------------------------------------ api
    def record_turn(self, result: Any, surface: str = "api") -> dict[str, Any] | None:
        """Log one turn and update the daily counters. Never raises."""
        if not self.enabled:
            return None
        try:
            event = build_event(result=result, salt=self.salt, surface=surface)
        except Exception:  # pragma: no cover - defensive
            logger.exception("telemetry event could not be built")
            return None

        self._count(event)
        self._emit(event)
        return event

    def record_counter(self, name: str, amount: int = 1) -> None:
        """Bump a standalone counter (429s, 503s) with no turn attached."""
        if not self.enabled:
            return
        self.counters.increment(name, amount=amount)

    def record_rate_limited(self, scope: str) -> None:
        self.record_counter("rate_limited")
        self.record_counter(f"rate_limited_{scope}")

    # -------------------------------------------------------------- internals
    def _count(self, event: dict[str, Any]) -> None:
        counters = self.counters
        counters.increment("turns")
        if event["blocked"]:
            counters.increment("blocked_turns")
        if event["semantic_retried"]:
            counters.increment("semantic_retried")
        if event["semantic_status"] in {"malformed", "unavailable"}:
            # After the retry, so this is the window with no semantic layer.
            counters.increment("semantic_malformed_final")
        if "soft_cue_fallback" in event["gate_layers"]:
            counters.increment("soft_cue_fallback_blocks")
        if event["audit_only"]:
            counters.increment("audit_only_near_miss")
        if event["fallback_steps"]:
            counters.increment("llm_fallback_used")
        if event["provider"] == "fake":
            counters.increment("fake_provider_answered")
        if event["provider"] == "overloaded" and event["quality_guard"] != "shed":
            # The wall-clock budget ran out. Counted so "the beta felt slow"
            # can be checked against how often it actually gave up. A turn shed
            # by the output guard wears the same reply but is counted under
            # degenerate_output_shed by the generator, not here.
            counters.increment("overloaded_turns")

    def _emit(self, event: dict[str, Any]) -> None:
        if self._writer is None:
            return
        try:
            self._writer.info(json.dumps(event, ensure_ascii=False, sort_keys=True))
        except Exception:  # pragma: no cover - defensive
            logger.exception("telemetry event could not be written")


#: The active telemetry sink, set at startup so code with no access to the
#: FastAPI app state - the LLM provider, deep inside a retry loop - can still
#: contribute a counter. Deliberately a single global: there is one process, and
#: threading a telemetry handle through the provider chain would be a worse
#: trade than this.
_ACTIVE: Telemetry | None = None


def set_active_telemetry(telemetry: Telemetry | None) -> None:
    global _ACTIVE
    _ACTIVE = telemetry


def count(name: str, amount: int = 1) -> None:
    """Bump a counter on the active sink, if there is one. Never raises."""
    sink = _ACTIVE
    if sink is None:
        return
    try:
        sink.record_counter(name, amount)
    except Exception:  # pragma: no cover - telemetry must never break a turn
        logger.exception("telemetry counter %s could not be recorded", name)


def build_telemetry(settings: Settings) -> Telemetry:
    telemetry = Telemetry(settings)
    set_active_telemetry(telemetry)
    return telemetry
