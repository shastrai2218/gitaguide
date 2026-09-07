"""Serialised pipeline admission: one turn at a time, with a bounded wait.

This exists because of a memory limit, not a throughput one. A turn's peak
allocation is two ONNX forward passes plus the retrieval set; one fits inside
Render's free 512 MB with a small margin and two do not, and exceeding it is a
SIGKILL - no traceback, just a 502 and a restart. The rate limiter reduces how
often concurrent turns arrive but cannot prevent it, because the first few
requests of a burst are inside the burst allowance by design.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.concurrency import PipelineBusyError, PipelineGate
from app.config import CURRENT_CONSENT_VERSION, Settings
from app.main import create_app
from app.pipeline.generation import OVERLOADED_ANSWER, PROVIDER_OVERLOADED


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
async def test_only_one_turn_runs_at_a_time() -> None:
    """The property the memory bound depends on."""
    gate = PipelineGate(timeout_seconds=5.0)
    concurrent = 0
    peak = 0

    async def turn() -> None:
        nonlocal concurrent, peak
        async with gate.admit():
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.05)
            concurrent -= 1

    await asyncio.gather(*(turn() for _ in range(6)))
    assert peak == 1, f"{peak} turns ran at once; peak memory would be {peak}x one turn"


async def test_a_queued_turn_is_served_not_refused() -> None:
    """Waiting is the normal case and must not look like an error."""
    gate = PipelineGate(timeout_seconds=5.0)
    waits: list[float] = []

    async def turn() -> None:
        async with gate.admit() as queued_for:
            waits.append(queued_for)
            await asyncio.sleep(0.05)

    await asyncio.gather(turn(), turn())

    assert len(waits) == 2, "both turns must be served"
    assert min(waits) < 0.01, "the first turn should not have waited"
    assert max(waits) >= 0.04, "the second turn should have queued behind it"


async def test_a_turn_that_cannot_start_in_time_is_shed() -> None:
    """An unbounded queue is worse than an honest refusal."""
    gate = PipelineGate(timeout_seconds=0.1)

    async def hog() -> None:
        async with gate.admit():
            await asyncio.sleep(1.0)

    holder = asyncio.create_task(hog())
    await asyncio.sleep(0.02)

    with pytest.raises(PipelineBusyError):
        async with gate.admit():
            pass  # pragma: no cover - admission must not succeed

    holder.cancel()


async def test_the_gate_is_released_when_a_turn_raises() -> None:
    """A failed turn must not wedge the pipeline for everyone else."""
    gate = PipelineGate(timeout_seconds=1.0)

    with pytest.raises(RuntimeError):
        async with gate.admit():
            raise RuntimeError("pipeline blew up")

    # If the semaphore leaked, this would time out instead of admitting.
    async with gate.admit():
        pass


async def test_queue_depth_is_observable() -> None:
    """``/health`` reports it, so a slow beta can be diagnosed without guessing."""
    gate = PipelineGate(timeout_seconds=5.0)
    assert gate.waiting == 0

    async def turn(hold: float) -> None:
        async with gate.admit():
            await asyncio.sleep(hold)

    first = asyncio.create_task(turn(0.15))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(turn(0.01))
    await asyncio.sleep(0.02)

    assert gate.waiting == 1, "the second turn should be counted as queued"
    await asyncio.gather(first, second)
    assert gate.waiting == 0


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #
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


def test_serialisation_is_on_by_default(client: TestClient) -> None:
    """Off would silently reintroduce the OOM it prevents."""
    assert Settings(mock_mode=True).pipeline_serialise is True
    components = client.get("/health").json()["components"]
    assert components["pipeline_serialised"] is True
    assert components["pipeline_queue_depth"] == 0


def test_a_shed_turn_returns_the_overload_reply_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tester sees a normal-shaped turn, so the UI needs no special case."""
    app = create_app(
        Settings(
            mock_mode=True,
            environment="test",
            rate_limit_enabled=False,
            consent_required=False,
            pipeline_queue_timeout_seconds=0.05,
        )
    )
    with TestClient(app) as client:
        gate = client.app.state.pipeline_gate
        assert gate is not None

        # Sync, not async: the call site does `async with gate.admit()`, so
        # admit() must raise when *called*, before the context manager protocol
        # is entered - which is exactly how a timed-out acquire behaves.
        def always_busy(*args: object, **kwargs: object):
            raise PipelineBusyError("busy")

        monkeypatch.setattr(gate, "admit", always_busy)

        response = client.post(
            "/api/chat", json={"session_id": "shed", "message": "I am overworked"}
        )

    assert response.status_code == 200, "shedding must not surface as a server error"
    body = response.json()
    assert body["reply"] == OVERLOADED_ANSWER
    assert body["meta"]["provider"] == PROVIDER_OVERLOADED
    assert body["blocked"] is False
    assert body["citations"] == []

    counters = client.app.state.telemetry.counters.today()
    assert counters["turns_shed"] == 1


def test_served_turns_are_counted_by_whether_they_queued(client: TestClient) -> None:
    """So "the beta felt slow" can be checked against how often turns waited."""
    client.post("/api/chat", json={"session_id": "gate-1", "message": "I am overworked"})

    counters = client.app.state.telemetry.counters.today()
    assert counters.get("turns_served_immediately", 0) == 1
    assert counters.get("turns_queued_then_served", 0) == 0
    assert counters.get("turns_shed", 0) == 0


def test_health_and_consent_are_not_serialised(client: TestClient) -> None:
    """Only the pipeline is bounded; cheap endpoints must stay responsive.

    Making them queue behind a 30s turn would be strictly worse for no memory
    saving - and a tester whose consent POST hangs cannot even start.
    """
    gate = client.app.state.pipeline_gate
    assert gate is not None

    # Hold the gate as a turn would, then confirm the cheap paths still answer.
    client.portal.call(gate._semaphore.acquire)
    try:
        assert client.get("/health").status_code == 200
        assert (
            client.post(
                "/api/consent",
                json={"session_id": "not-blocked", "version": CURRENT_CONSENT_VERSION},
            ).status_code
            == 200
        )
    finally:
        gate._semaphore.release()
