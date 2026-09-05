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
    GateSelfTestError,
    run_gate_self_test,
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

    def explode() -> None:
        raise GateSelfTestError("refusing to start: simulated disarmed gate")

    monkeypatch.setattr(main, "run_gate_self_test", explode)
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
