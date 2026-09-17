"""The boot-time gate self-test, including that it actually refuses to start.

A self-test that cannot fail proves nothing, so the important tests here are
the negative ones: a gate with the crisis rules removed must abort startup, and
a gate that blocks everything must abort startup too. Both directions matter -
rules that are gone leave testers unprotected, rules that are too broad meet
every tester with a helpline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.gate_selftest import (
    CANONICAL_CONTROL,
    CANONICAL_CRISIS,
    MIN_GENERATION_BUDGET_SECONDS,
    GateSelfTestError,
    StartupConfigError,
    check_generation_budget,
    run_gate_self_test,
    run_startup_self_test,
)
from app.pipeline.safety import RULES, SafetyGate


def test_the_self_test_passes_with_the_real_rules() -> None:
    run_gate_self_test()


def test_a_real_app_starts(caplog: pytest.LogCaptureFixture) -> None:
    """The check runs on the real startup path, not only when called directly."""
    app = create_app(Settings(mock_mode=True, environment="test"))
    with caplog.at_level("INFO"), TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert any("self-test passed" in record.message for record in caplog.records)


def test_startup_is_refused_when_the_crisis_rules_are_gone() -> None:
    """The failure this exists to catch: a silently disarmed rule layer."""
    disarmed = SafetyGate(rules=[])

    with pytest.raises(GateSelfTestError) as failure:
        run_gate_self_test(disarmed)

    message = str(failure.value)
    assert "refusing to start" in message
    assert "not armed" in message
    assert "app/pipeline/safety.py" in message, "the error must say where to look"


def test_startup_is_refused_when_the_rules_block_everything() -> None:
    """Too broad is also a failure: testers would be met with a helpline."""
    from app.pipeline.safety import SafetyCategory, _rule

    over_broad = SafetyGate(
        rules=[
            *RULES,
            _rule("catch_all", SafetyCategory.SEVERE_DISTRESS, r".", "matches anything"),
        ]
    )

    with pytest.raises(GateSelfTestError) as failure:
        run_gate_self_test(over_broad)
    assert "too broad" in str(failure.value)


def test_the_app_will_not_start_with_a_disarmed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: a broken gate takes the process down, it does not warn."""
    import app.main as main

    def explode(*args: object, **kwargs: object) -> None:
        raise GateSelfTestError("refusing to start: simulated disarmed gate")

    monkeypatch.setattr(main, "run_startup_self_test", explode)
    app = create_app(Settings(mock_mode=True, environment="test"))

    with pytest.raises(GateSelfTestError), TestClient(app):
        pass  # pragma: no cover - startup raises before the body runs


def test_the_canonical_cases_are_what_they_claim_to_be() -> None:
    """Guard the fixtures themselves against drifting into meaninglessness."""
    gate = SafetyGate()
    neutral = gate.check_input(CANONICAL_CONTROL, None, None, "s")
    crisis = gate.check_input(CANONICAL_CRISIS, None, None, "s")

    assert crisis.blocked and "rule" in crisis.layers
    assert not neutral.blocked
    # The control must be a plausible tester message, not a nonsense string.
    assert len(CANONICAL_CONTROL.split()) >= 5


# The generation-budget floor
def test_the_default_budget_passes_the_floor() -> None:
    check_generation_budget(Settings(mock_mode=True))


def test_a_budget_below_the_floor_refuses_to_start() -> None:
    """The 2s budget that starved the classifier on 5 Sep must not be bootable."""
    with pytest.raises(StartupConfigError) as failure:
        check_generation_budget(
            Settings(mock_mode=True, generation_budget_seconds=2.0)
        )

    message = str(failure.value)
    assert "refusing to start" in message
    assert "starves" in message
    # The error has to say what to change, not just that something is wrong.
    assert "GITAGUIDE_GENERATION_BUDGET_SECONDS" in message


def test_the_floor_is_inclusive() -> None:
    """Exactly at the floor is allowed; the floor is a minimum, not a gap."""
    check_generation_budget(
        Settings(mock_mode=True, generation_budget_seconds=MIN_GENERATION_BUDGET_SECONDS)
    )
    with pytest.raises(StartupConfigError):
        check_generation_budget(
            Settings(
                mock_mode=True,
                generation_budget_seconds=MIN_GENERATION_BUDGET_SECONDS - 0.1,
            )
        )


def test_a_low_budget_is_rejected_rather_than_clamped() -> None:
    """Clamping would run a number nobody chose and say nothing about it."""
    settings = Settings(mock_mode=True, generation_budget_seconds=1.0)
    with pytest.raises(StartupConfigError):
        run_startup_self_test(settings)
    assert settings.generation_budget_seconds == 1.0, "the value must not be mutated"


def test_the_app_will_not_start_with_a_budget_below_the_floor() -> None:
    """End to end: a bad env var takes startup down, it does not warn."""
    app = create_app(
        Settings(mock_mode=True, environment="test", generation_budget_seconds=5.0)
    )
    with pytest.raises(StartupConfigError), TestClient(app):
        pass  # pragma: no cover - startup raises before the body runs


# The router lexicon check: loud, but not fatal
#
# This one warns where every check above refuses to start, and the asymmetry is
# deliberate. An unarmed safety gate is a service that is up and not protecting
# anyone. A missing lexicon costs latency and NIM quota - every "hi" pays a
# model call - while every answer stays correct and safe, so taking a working
# beta down over it would do more harm than the fault does.
def test_the_lexicon_check_passes_with_the_real_file(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from app.pipeline.gate_selftest import check_router_lexicon

    with caplog.at_level(logging.INFO):
        count = check_router_lexicon()
    assert count > 0
    assert "router lexicon loaded" in caplog.text


def test_a_missing_lexicon_is_shouted_about_but_does_not_stop_the_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The 16 Sep production failure, as a test.

    The image had no data/ directory, so the lexicon never loaded and stage 1
    was disabled on every turn. Nothing said so except one exception line at
    first use. Now the boot says it, at CRITICAL, and names the likely cause.
    """
    import logging
    import pathlib

    import app.pipeline.router as router
    from app.pipeline.gate_selftest import check_router_lexicon

    monkeypatch.setattr(router, "LEXICON_PATH", pathlib.Path("/nonexistent/lexicon.yaml"))
    monkeypatch.setattr(router, "_LEXICON", None)

    with caplog.at_level(logging.CRITICAL):
        count = check_router_lexicon()

    assert count == 0
    assert "ROUTER LEXICON DID NOT LOAD" in caplog.text
    assert "Dockerfile" in caplog.text, "the message must name the likely cause"
    assert any(
        record.levelno >= logging.CRITICAL for record in caplog.records
    ), "a warning nobody sees is what we already had"


def test_the_whole_startup_self_test_survives_a_missing_lexicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degrade, do not crash - the request-time contract, kept at boot too."""
    import pathlib

    import app.pipeline.router as router
    from app.pipeline.gate_selftest import run_startup_self_test

    monkeypatch.setattr(router, "LEXICON_PATH", pathlib.Path("/nonexistent/lexicon.yaml"))
    monkeypatch.setattr(router, "_LEXICON", None)

    run_startup_self_test(Settings(mock_mode=True, environment="test"))


def test_a_missing_lexicon_still_answers_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request-time behaviour is unchanged: guidance, not an error."""
    import asyncio
    import pathlib

    import app.pipeline.router as router

    monkeypatch.setattr(router, "LEXICON_PATH", pathlib.Path("/nonexistent/lexicon.yaml"))
    monkeypatch.setattr(router, "_LEXICON", None)

    decision = asyncio.run(router.ConversationRouter(llm=None).route("hi"))
    assert decision.route is router.Route.GUIDANCE
    assert decision.retrieves
