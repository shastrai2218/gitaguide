"""The frontend's behaviour when the network is the problem, not the answer.

Tester item 9, verbatim: *"Now also handles the error giving response if answer
is coming late or internet disconnected or taking long time for response or I
found the problem like I asked same queries 1st time but not get response but
in second time I am getting answer perfectly."*

Three complaints in one sentence - slow, disconnected, and a first attempt that
failed where the second worked - and the server-side evidence agrees with the
middle one: 15 turns were admitted to the pipeline on 9 Sep and 2 completed,
which is what a client going away mid-request looks like (Phase 0).

What this module can and cannot check
-------------------------------------
There is no JavaScript test runner in this repository and this task is not the
place to introduce one, so these are **source-level** assertions: they hold the
mechanisms in place and would fail if someone removed the retry, the offline
check or the draft handoff. They do not execute the code.

The behaviour itself was verified in a browser against the dev server on
9 Sep 2026, and the results are recorded in kg_measurements.md section 14:

* a rejected ``fetch`` produced exactly **two** calls - one retry, not a loop;
* the failed message was **restored to the composer**;
* with ``navigator.onLine`` false, a send made **zero** fetch calls and showed
  the offline copy;
* the offline notice cleared on the ``online`` event.

Anyone changing this file should re-run that check rather than trusting these
greps alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"


@pytest.fixture(scope="module")
def api_js() -> str:
    return (SRC / "api.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_jsx() -> str:
    return (SRC / "App.jsx").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def composer_jsx() -> str:
    return (SRC / "components" / "Composer.jsx").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def waiting_jsx() -> str:
    return (SRC / "components" / "WaitingIndicator.jsx").read_text(encoding="utf-8")


# Failures are told apart, because the right response differs
@pytest.mark.parametrize("kind", ["OFFLINE", "NETWORK", "TIMEOUT", "SERVER"])
def test_failure_kinds_are_named(api_js: str, kind: str) -> None:
    assert f"{kind}:" in api_js, "the UI cannot react to a cause it cannot name"


def test_offline_is_detected_from_the_browser(api_js: str) -> None:
    assert "navigator.onLine === false" in api_js
    assert "export function isOffline" in api_js


def test_an_offline_send_never_reaches_the_network(api_js: str) -> None:
    """Nothing is sent, so nothing can be half-sent.

    Verified in the browser: zero fetch calls while offline.
    """
    live = api_js.split("async function postChatLive")[1].split("async function postChatAttempt")[0]
    assert "isOffline()" in live
    assert "FAILURE.OFFLINE" in live
    # The guard has to come before the attempt, or a request goes out anyway.
    assert live.index("isOffline()") < live.index("postChatAttempt(")


# Exactly one automatic retry, and only of the right thing
def test_a_dropped_connection_is_retried_once(api_js: str) -> None:
    live = api_js.split("async function postChatLive")[1].split("async function postChatAttempt")[0]
    assert live.count("postChatAttempt(") == 2, (
        "exactly two attempts: the original and one retry. Verified in the browser "
        "as two fetch calls."
    )
    assert "NETWORK_RETRY_DELAY_MS" in live


def test_the_timeout_is_not_auto_retried(api_js: str) -> None:
    """The server is probably still working; a second turn costs two more NIM
    calls and another turn's memory on a 0.1-CPU instance for the same answer."""
    live = api_js.split("async function postChatLive")[1].split("async function postChatAttempt")[0]
    assert "FAILURE.NETWORK" in live
    assert "FAILURE.TIMEOUT" not in live, "a timeout must not trigger the retry"


def test_a_server_answer_is_not_auto_retried(api_js: str) -> None:
    """A 429 already carries its wait, and a 4xx will not change its mind."""
    live = api_js.split("async function postChatLive")[1].split("async function postChatAttempt")[0]
    assert "error.kind === FAILURE.NETWORK" in live


def test_a_caller_cancellation_is_not_retried(api_js: str) -> None:
    """New session, or an unmount: the user did that deliberately."""
    live = api_js.split("async function postChatLive")[1].split("async function postChatAttempt")[0]
    assert "signal?.aborted" in live


# The message survives the failure
def test_the_composer_text_is_owned_by_app(app_jsx: str, composer_jsx: str) -> None:
    """It has to be, for a failed turn to be able to put it back."""
    assert "const [draft, setDraft] = useState('')" in app_jsx
    assert "value={draft}" in app_jsx
    assert "onChange={setDraft}" in app_jsx
    assert "function Composer({ value, onChange, onSend, busy, online = true })" in composer_jsx
    assert "value={value}" in composer_jsx, "the textarea must be controlled"


def test_a_failed_turn_restores_the_draft(app_jsx: str) -> None:
    """Verified in the browser: the text was back in the composer."""
    send_body = app_jsx.split("const send = useCallback")[1].split("const sendFromComposer")[0]
    assert send_body.count("setDraft(text)") >= 2, (
        "the draft must be restored on both the consent path and the error path"
    )


def test_being_asked_to_consent_does_not_eat_the_message(app_jsx: str) -> None:
    consent_branch = app_jsx.split("error.code === CONSENT_REQUIRED")[1].split("}")[0]
    assert "setDraft(text)" in consent_branch


def test_resending_the_same_text_replaces_the_error_rather_than_stacking(
    app_jsx: str,
) -> None:
    """Otherwise the transcript shows the message twice with a failure between."""
    assert "isRetryOfSameText" in app_jsx
    assert "last?.kind === 'error' && last.text === text" in app_jsx
    assert "replaceLast: isRetryOfSameText" in app_jsx


def test_a_new_session_clears_the_draft(app_jsx: str) -> None:
    handler = app_jsx.split("function handleNewSession()")[1].split("}")[0]
    assert "setDraft('')" in handler


def test_the_error_turn_still_offers_its_own_retry(app_jsx: str) -> None:
    """Two ways forward: resend as written, or edit it in the composer first."""
    assert "onRetry={() => send(entry.text, { replaceLast: true })}" in app_jsx


# Connection state is visible before a send fails
def test_the_app_listens_for_connection_changes(app_jsx: str) -> None:
    for event in ("'online'", "'offline'"):
        assert f"window.addEventListener({event}" in app_jsx
        assert f"window.removeEventListener({event}" in app_jsx, "listeners must be cleaned up"


def test_the_composer_says_when_it_is_offline(composer_jsx: str) -> None:
    assert "{!online &&" in composer_jsx
    assert "composer-offline" in composer_jsx
    assert 'role="status"' in composer_jsx, "it must be announced, not only shown"


def test_the_offline_notice_has_a_style(app_jsx: str) -> None:
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    assert ".composer-offline" in css


# Waiting copy, pinned to measured latency
def test_the_waiting_copy_escalates_and_ends_honestly(waiting_jsx: str) -> None:
    stages = [int(value) for value in re.findall(r"at:\s*(\d+)", waiting_jsx)]
    assert stages == sorted(stages), "stages must be in order"
    assert stages[0] == 0
    assert len(stages) >= 4, "one spinner for a minute reads as a hang"


def test_the_waking_up_claim_is_no_longer_made_at_35_seconds(waiting_jsx: str) -> None:
    """It was a guess, and wrong for a warm turn measured at 37.2s.

    The stage that mentions waking up now also names model load, because the
    browser cannot tell the two apart and asserting one is how you tell a
    waiting tester something false.
    """
    stages = [int(value) for value in re.findall(r"at:\s*(\d+)", waiting_jsx)]
    assert 35000 not in stages

    waking_stage = waiting_jsx.split("waking up")[0]
    tail = waking_stage[waking_stage.rfind("at:") :]
    assert "30000" in tail, "the both-causes copy should arrive at 30s"
    assert "under load" in waiting_jsx or "model is under load" in waiting_jsx


def test_the_last_stage_promises_the_message_is_kept(waiting_jsx: str) -> None:
    """Because it is - the draft is restored. The copy must not lie about it."""
    assert "send it again" in waiting_jsx
