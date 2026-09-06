"""Serialise pipeline execution so peak memory is one turn, not N.

Why
---
A turn's peak allocation is dominated by two ONNX forward passes and the
retrieval result set. One turn fits inside Render's free 512 MB with a small
margin; two concurrent turns do not, and the kernel's answer to that is SIGKILL
- no traceback, no log line, just a 502 and a restart. The rate limiter reduces
how often that happens but cannot prevent it: a burst arrives together, and its
first few requests are *inside* the burst allowance.

So the pipeline runs one turn at a time. This is not a throughput control - at
16 turns/minute globally there is no throughput to protect - it is a memory
bound, expressed where memory is actually spent.

What a queued tester experiences
--------------------------------
They wait. That is honest and usually short: a turn is ~10-35s, so a queue of
one resolves inside the patience copy the UI already shows. What must not happen
is an unbounded wait behind a queue that keeps growing, so a turn that cannot
start within ``pipeline_queue_timeout_seconds`` is shed with the same overload
reply the generation budget uses - a normal-shaped turn, not a 500.

Only the pipeline is serialised. ``/health``, consent and feedback allocate
almost nothing and stay concurrent; making them queue behind a 30s turn would
be strictly worse for no memory saving.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class PipelineBusyError(RuntimeError):
    """A turn could not start within the queue timeout."""


class PipelineGate:
    """One-at-a-time admission to the pipeline, with a bounded wait.

    Deliberately a semaphore of exactly one rather than a configurable width:
    the bound exists because two concurrent turns do not fit in 512 MB, and a
    width of two would silently reintroduce the failure it prevents. Raising it
    is a memory decision, so it belongs with the memory measurements in
    docs/DEPLOY.md rather than in an environment variable.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(1)
        self._waiting = 0

    @property
    def waiting(self) -> int:
        """How many turns are queued right now. Exposed for ``/health``."""
        return self._waiting

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[float]:
        """Hold the gate for one turn.

        Yields the seconds spent queueing, so the caller can record whether a
        turn was served immediately or after a wait. Raises
        :class:`PipelineBusyError` if the wait would exceed the timeout.
        """
        started = time.perf_counter()
        self._waiting += 1
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            self._waiting -= 1
            logger.warning(
                "shedding a turn: waited %.1fs for the pipeline (%d already queued)",
                time.perf_counter() - started,
                self._waiting,
            )
            raise PipelineBusyError(
                f"the pipeline did not become free within {self.timeout_seconds:.0f}s"
            ) from exc

        self._waiting -= 1
        queued_for = time.perf_counter() - started
        if queued_for > 0.05:
            logger.info("turn queued for %.1fs before starting", queued_for)
        try:
            yield queued_for
        finally:
            self._semaphore.release()
