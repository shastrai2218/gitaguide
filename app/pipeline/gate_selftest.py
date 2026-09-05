"""Boot-time proof that the deterministic safety layer is actually armed.

Why this exists
---------------
The rule layer is a table of regexes. Nothing about a disabled rule is visible:
the service starts, ``/health`` is green, every test that ran hours ago on a
developer's machine still passes, and messages that should stop simply do not.
The Day 3 miss (docs/kg_measurements.md section 11) was a version of exactly
this - a phrasing no rule covered - and it was found by accident, days later,
while measuring something else.

So the gate proves itself on every boot, before the first request:

* one canonical hard-rule phrase **must** block, and
* one canonical benign control **must not**.

Both checks run **in-process against the rule layer only**. No NIM call, no
network, no model: a self-test that depends on an external service would fail
for reasons that have nothing to do with the gate, and would soon be disabled
by whoever got tired of it. It costs two regex sweeps.

On failure the process **refuses to start**. A GitaGuide with a silently
disabled crisis rule must not be deployable - a service that is up and not
protecting anyone is worse than one that is visibly down, because nobody goes
looking for the second kind of failure.
"""

from __future__ import annotations

import logging

from app.pipeline.safety import SafetyGate
from app.pipeline.schemas import EmotionResult, IntentResult

logger = logging.getLogger(__name__)

#: Must block, on the rule layer alone. Chosen to be the least ambiguous
#: phrasing in the table, so a failure means the layer is broken rather than
#: that a judgement call changed.
CANONICAL_CRISIS = "I want to kill myself"

#: Must not block. An ordinary workplace message with no crisis content - if
#: this ever blocks, the rules have become too broad and every tester is about
#: to be met with a helpline for asking about their manager.
CANONICAL_CONTROL = "My manager overruled my design decision again today"


class GateSelfTestError(RuntimeError):
    """The safety gate did not behave as required at startup."""


def run_gate_self_test(gate: SafetyGate | None = None) -> None:
    """Assert the rule layer blocks and permits the canonical cases.

    Raises :class:`GateSelfTestError` on any deviation. Deliberately takes no
    intent and no emotion: passing a neutral :class:`IntentResult` means the
    semantic layer contributes nothing, so this exercises the deterministic
    layer in isolation. If it passes, the rules are armed.
    """
    gate = gate or SafetyGate()
    neutral_emotion = EmotionResult()
    neutral_intent = IntentResult()

    failures: list[str] = []

    crisis = gate.check_input(CANONICAL_CRISIS, neutral_emotion, neutral_intent, "selftest")
    if not crisis.blocked:
        failures.append(
            f"the canonical crisis phrase was NOT blocked by the rule layer "
            f"({CANONICAL_CRISIS!r}); the deterministic safety rules are not armed"
        )
    elif "rule" not in crisis.layers:
        failures.append(
            f"the canonical crisis phrase blocked, but not on the rule layer "
            f"(layers={crisis.layers}); the rule table is not doing the work"
        )

    control = gate.check_input(CANONICAL_CONTROL, neutral_emotion, neutral_intent, "selftest")
    if control.blocked:
        failures.append(
            f"the canonical benign control WAS blocked (layers={control.layers}); "
            "the rules are too broad and ordinary messages will be refused"
        )

    if failures:
        detail = "; ".join(failures)
        logger.critical("SAFETY GATE SELF-TEST FAILED: %s", detail)
        raise GateSelfTestError(
            "refusing to start: " + detail + ". Fix the rule table in "
            "app/pipeline/safety.py, or run pytest tests/test_safety.py to see "
            "which rule regressed."
        )

    logger.info(
        "safety gate self-test passed (%d rules armed; crisis blocked, control allowed)",
        len(gate.rules),
    )
