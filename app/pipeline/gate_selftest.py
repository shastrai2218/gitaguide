"""Boot-time proof that the safety pipeline is actually armed.

Called by: app.main, once at startup.

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

from app.config import Settings, get_settings
from app.pipeline.safety import SafetyGate
from app.pipeline.schemas import EmotionResult, IntentResult

logger = logging.getLogger(__name__)

#: Lowest generation budget the pipeline may boot with, in seconds.
#:
#: The budget is shared across the fallback chain - each attempt gets
#: ``remaining / providers_left`` - and the **intent classifier uses that same
#: chain**. So the budget is not only a generation control: it also bounds the
#: semantic half of the crisis gate.
#:
#: With the two-model NIM chain the first attempt gets half the budget. The
#: worst intent call observed on Day 3 took 6.3s (p50 1.2s), so a 15s budget
#: leaves 7.5s for it and the classifier still fits. Below that the classifier
#: starts timing out, the gate silently drops to its rule layer plus the
#: soft-cue fallback, and the failure looks like nothing at all.
#:
#: This was observed, not theorised: a forced 2s budget on 5 Sep 2026 produced
#: ``semantic_malformed_final: 1`` on a single turn.
MIN_GENERATION_BUDGET_SECONDS = 15.0

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


class StartupConfigError(RuntimeError):
    """A configured value would silently disable part of the safety pipeline."""


def check_generation_budget(settings: Settings | None = None) -> None:
    """Refuse a budget so small it would starve the intent/safety classifier.

    Raises :class:`StartupConfigError` rather than clamping the value. Clamping
    would mean the deployment runs with a number nobody chose and the operator
    never learns their configuration was ignored.
    """
    settings = settings or get_settings()
    budget = settings.generation_budget_seconds
    if budget >= MIN_GENERATION_BUDGET_SECONDS:
        return

    detail = (
        f"generation_budget_seconds is {budget:g}s, below the {MIN_GENERATION_BUDGET_SECONDS:g}s "
        "floor. The budget is shared across the provider chain and the intent "
        "classifier runs on that same chain, so a budget this small starves the "
        "semantic half of the crisis gate: it times out, the gate quietly falls "
        "back to the rule layer plus the soft-cue fallback, and nothing in the "
        "response says so. Raise GITAGUIDE_GENERATION_BUDGET_SECONDS to at least "
        f"{MIN_GENERATION_BUDGET_SECONDS:g}."
    )
    logger.critical("STARTUP CONFIG REJECTED: %s", detail)
    raise StartupConfigError("refusing to start: " + detail)


#: Salt values that mean "nobody set one". The code default is public in this
#: repository, so a deployment running on it has pseudonyms anyone can
#: recompute from a session id; the template's placeholder is what a copied
#: `.env.vm.example` carries until someone edits it; empty is unset.
PLACEHOLDER_SALTS: frozenset[str] = frozenset({"", "gitaguide-beta"})
PLACEHOLDER_SALT_MARKER = "REPLACE_ME"


def check_telemetry_salt(settings: Settings | None = None) -> None:
    """In prod, refuse to start on a salt that is not actually a secret.

    Telemetry never stores a session id, only ``sha256(salt + session_id)``
    (:func:`app.telemetry.hash_session`), which is a pseudonym exactly as long
    as the salt is private. Three values make it public: unset, the literal
    placeholder from ``.env.vm.example``, and the code default, which this
    repository publishes. None of them errors anywhere - the log fills up with
    hashes that look fine and are reversible by anyone holding a session id.

    Outside prod this is a no-op: a laptop, a test and the CI matrix all run on
    the default salt and should keep doing so. In prod the check raises
    :class:`StartupConfigError` rather than substituting a random value, for
    the same reason :func:`check_generation_budget` does not clamp - a salt
    generated at boot would change on every restart, quietly, and the operator
    would never learn the variable was ignored.
    """
    settings = settings or get_settings()
    if not settings.is_prod:
        return
    salt = settings.telemetry_session_salt
    if salt not in PLACEHOLDER_SALTS and PLACEHOLDER_SALT_MARKER not in salt.upper():
        return

    detail = (
        "GITAGUIDE_TELEMETRY_SESSION_SALT is "
        + ("unset" if not salt else "a placeholder" if PLACEHOLDER_SALT_MARKER in salt.upper() else "the public code default")
        + ". Telemetry pseudonymises session ids by hashing them with this salt, "
        "so a salt that is empty, copied from .env.vm.example unedited, or the "
        "default that ships in the repository makes every pseudonym reversible "
        "by anyone who knows a session id. Set it to a long random string once "
        "and keep it."
    )
    logger.critical("STARTUP CONFIG REJECTED: %s", detail)
    raise StartupConfigError("refusing to start: " + detail)


def check_router_lexicon() -> int:
    """Say loudly, at boot, whether the stage-1 lexicon actually loaded.

    Returns the number of phrases, so a caller can assert on it.

    This **warns and continues** where the checks above refuse to start, and the
    asymmetry is the point. An unarmed safety gate is a service that is up and
    not protecting anyone, so it must not be deployable. A missing lexicon costs
    latency and NIM quota - every "hi" pays a model call - but every answer is
    still correct and safe, and taking a working beta down over it would do more
    harm than the fault does.

    What it prevents is the fault being *invisible*. On 16 Sep 2026 the Render
    image had no ``data/`` directory at all: the Dockerfile copied ``app``,
    ``corpus``, ``kg``, ``eval`` and ``scripts``, and nothing else. Stage 1 was
    disabled for every message on every turn, and the only trace was one
    exception line at first use, hours into the log. A smoke test found it, four
    days after the lexicon shipped.
    """
    from app.pipeline.router import LEXICON_PATH, get_lexicon  # local: import cycle

    lexicon = get_lexicon()
    count = len(lexicon.phrases)
    if count:
        logger.info(
            "router lexicon loaded: %d phrases, %d fillers, bound %d words",
            count,
            len(lexicon.fillers),
            lexicon.max_message_words,
        )
        return count

    logger.critical(
        "ROUTER LEXICON DID NOT LOAD (expected at %s). Stage 1 is disabled, so "
        "every greeting, thanks and meta question now costs a model call and "
        "routing accuracy drops to the model alone. The service still answers "
        "correctly - this is cost and latency, not safety - so it is starting "
        "anyway. Most likely the deployment image does not contain data/: check "
        "the COPY lines in the Dockerfile.",
        LEXICON_PATH,
    )
    return 0


def run_startup_self_test(
    settings: Settings | None = None, gate: SafetyGate | None = None
) -> None:
    """Every boot-time assertion, in one call for :mod:`app.main`.

    Ordered cheapest first, and config before behaviour: a rejected budget or
    salt is a typo in an environment variable, and saying so before the model
    checks run makes the error easier to act on. The lexicon check runs last
    because it is the only one that does not refuse to start.
    """
    check_generation_budget(settings)
    check_telemetry_salt(settings)
    run_gate_self_test(gate)
    check_router_lexicon()


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
