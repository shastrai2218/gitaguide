"""Production smoke test: prove a deployment is live, safe, and honest.

Run against the deployed API before letting anyone near it::

    python -m scripts.prod_smoke https://13-205-163-41.sslip.io
    python -m scripts.prod_smoke https://... --skip-rate-limit

What it is for
--------------
Every check here corresponds to a way this system has actually been observed to
look healthy while being wrong:

* ``mock_mode`` true would serve canned answers from in-process fakes with
  ``/health`` returning 200 and citations attached to every reply.
* An in-memory repository stores nothing, and used to report itself as
  ``"sqlalchemy"`` whether it was PostgreSQL or SQLite.
* The retriever silently falls back to an in-memory implementation when Neo4j
  raises, which is a different graph with different retrieval semantics.
* A blocked turn that still ran retrieval and generation would mean the gate
  short-circuit is not short-circuiting.
* Consent that is not enforced server-side makes the consent record worthless.
* A rate limiter probed sequentially looks disabled even when it is working,
  because a token bucket refills faster than a slow caller drains it - so the
  burst here is sent concurrently.

So this asserts the *evidence*, not the absence of errors.

Read-only, with one exception it owns: it creates a single session of its own,
sends a handful of turns through it, and leaves one feedback row. It never
deletes anything, never reads another session, and never touches configuration.
It does not print credentials.

Exit code 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

PASS = "  [PASS]"
FAIL = "  [FAIL]"
INFO = "  [info]"

#: Blocked by the deterministic rule layer, with no dependence on the semantic
#: classifier - the same phrase the startup self-test uses.
BLOCKED_MESSAGE = "I want to kill myself"

#: The notice version testers are asked to accept. Literal rather than imported
#: so this script stays stdlib-only and runnable from any machine;
#: tests/test_tester_pack.py asserts it matches app.config.CURRENT_CONSENT_VERSION.
CONSENT_VERSION = "beta-1"
#: The pre-launch placeholder. The server must refuse it, or the bump did not
#: actually re-ask anyone.
SUPERSEDED_CONSENT_VERSION = "v1"

#: An ordinary workplace dilemma. Must produce a normal answer with citations.
CONTROL_MESSAGE = (
    "My manager overrules every decision I make and now my team asks him "
    "instead of me. I feel like I have no control over my own work."
)

#: A greeting, which must be answered as one. Before Phase 1b this came back
#: with a verse about detachment from the fruits of action, because every
#: message ran retrieval. The check is deliberately on the citation count and
#: the length rather than on the wording: what is being verified is that the
#: router is actually wired up in the deployed build, and a greeting that cites
#: nothing and stays short is the observable consequence of that.
GREETING_MESSAGE = "hi"
#: The greeting tier asks for 15-40 words. The ceiling here is loose on purpose
#: - this is a smoke test, not the measurement. A reply over it is a reply that
#: was written by the guidance prompt.
GREETING_MAX_WORDS = 60


class Smoke:
    """Accumulates results so one failure does not hide the rest."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = f"smoke-{uuid.uuid4().hex[:12]}"
        self.failures: list[str] = []
        self.checks = 0

    # ------------------------------------------------------------- plumbing
    def request(
        self, path: str, payload: dict | None = None
    ) -> tuple[int, object, dict[str, str]]:
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, json.load(response), dict(response.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                body: object = json.loads(raw)
            except ValueError:
                body = raw
            return exc.code, body, dict(exc.headers)
        except Exception as exc:  # network, DNS, TLS, timeout
            return 0, {"error": str(exc)}, {}

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        print(f"{PASS if ok else FAIL} {label}" + (f" - {detail}" if detail else ""))
        if not ok:
            self.failures.append(label)
        return ok

    def section(self, title: str) -> None:
        print(f"\n{title}")

    # ---------------------------------------------------------------- checks
    def health(self) -> dict | None:
        self.section("1. /health - is this a real deployment?")
        status, body, _ = self.request("/health")
        if not self.check(status == 200, "health responds 200", f"got {status}"):
            print(f"{INFO} {body}")
            return None

        assert isinstance(body, dict)
        components = body.get("components", {})

        self.check(
            body.get("mock_mode") is False,
            "mock_mode is false",
            "TRUE means every answer is canned" if body.get("mock_mode") else "",
        )
        self.check(
            components.get("mock_mode") is False,
            "components agree mock_mode is false",
        )

        emotion = components.get("emotion_backend", "")
        self.check(
            emotion == "onnx-int8-go_emotions",
            "emotion backend is the real ONNX model",
            f"got {emotion!r}",
        )

        retrieval = components.get("retrieval_backend", "")
        self.check(
            retrieval == "neo4j-hybrid-cypher",
            "retrieval backend is Neo4j",
            f"got {retrieval!r} - the in-memory retriever is a different graph",
        )

        repository = components.get("repository", "")
        self.check(
            repository.startswith("postgresql"),
            "repository is PostgreSQL",
            f"got {repository!r}",
        )
        self.check(components.get("database_ok") is True, "database reachable")

        provider = components.get("llm_provider", "")
        self.check(
            "nvidia" in provider,
            "LLM chain is NVIDIA NIM",
            f"got {provider!r}",
        )
        self.check(
            "fake" not in provider,
            "the chain has no fake last resort",
            f"got {provider!r}",
        )

        counters = components.get("telemetry_today")
        self.check(isinstance(counters, dict), "telemetry counters are exposed")
        limits = components.get("rate_limit")
        self.check(isinstance(limits, dict), "rate limiter is active")
        if isinstance(limits, dict):
            print(
                f"{INFO} global {limits.get('global_per_minute')}/min "
                f"(burst {limits.get('global_capacity')})"
            )
        return body

    def self_test_evidence(self) -> None:
        """The startup self-test cannot be queried, so infer it from liveness.

        If the gate self-test or the budget floor had failed, the process would
        not have started and ``/health`` could not have answered. A reachable
        service *is* the evidence - stated here so the check is not mistaken for
        an unverified claim.
        """
        self.section("2. startup self-test")
        status, _, _ = self.request("/health")
        self.check(
            status == 200,
            "startup self-test passed",
            "the process refuses to boot with disarmed rules or a budget "
            "below the floor, so a reachable service means both passed",
        )
        print(f"{INFO} rule count is logged at boot: grep 'rules armed' in the API logs")

    def consent(self) -> None:
        self.section("3. consent is enforced server-side, at the current notice version")
        status, body, _ = self.request(
            "/api/chat", {"session_id": self.session, "message": "hello"}
        )
        detail = body.get("detail", {}) if isinstance(body, dict) else {}
        code = detail.get("code") if isinstance(detail, dict) else None
        self.check(
            status == 409 and code == "consent_required",
            "chat without consent is refused with 409",
            f"got {status} code={code}",
        )
        required = detail.get("required_version") if isinstance(detail, dict) else None
        self.check(
            required == CONSENT_VERSION,
            "the server asks for the current notice version",
            f"got {required!r}, expected {CONSENT_VERSION!r} - the frontend sends the "
            "latter, so a mismatch means nobody can get past the gate",
        )

        # The superseded version must be refused. If it is accepted, the bump
        # changed a string and re-asked nobody.
        status, _, _ = self.request(
            "/api/consent",
            {"session_id": self.session, "version": SUPERSEDED_CONSENT_VERSION},
        )
        self.check(
            status == 400,
            f"the superseded notice version {SUPERSEDED_CONSENT_VERSION!r} is refused",
            f"got {status}",
        )

        status, body, _ = self.request(
            "/api/consent", {"session_id": self.session, "version": CONSENT_VERSION}
        )
        self.check(status == 200, "consent at the current version is recorded", f"got {status}")
        if isinstance(body, dict):
            self.check(
                body.get("consent_version") == CONSENT_VERSION,
                "the recorded version round-trips",
                f"got {body.get('consent_version')!r}",
            )
            print(f"{INFO} version={body.get('consent_version')} at={body.get('consent_at')}")

    def control_turn(self) -> str | None:
        self.section("4. a normal workplace turn")
        started = time.perf_counter()
        status, body, _ = self.request(
            "/api/chat", {"session_id": self.session, "message": CONTROL_MESSAGE}
        )
        elapsed = time.perf_counter() - started
        if not self.check(status == 200, "chat after consent returns 200", f"got {status}"):
            print(f"{INFO} {body}")
            return None

        assert isinstance(body, dict)
        self.check(body.get("blocked") is False, "an ordinary message is not blocked")
        self.check(bool(body.get("reply", "").strip()), "the reply is not empty")
        citations = body.get("citations") or []
        self.check(len(citations) > 0, "the reply cites verses", f"{len(citations)} citation(s)")

        meta = body.get("meta") or {}
        model = meta.get("model") or ""
        self.check(
            "nemotron" in model.lower(),
            "a real NIM model answered",
            f"got {model!r}",
        )
        self.check(
            meta.get("provider") != "overloaded",
            "the turn was not an overload fallback",
        )
        print(f"{INFO} {elapsed:.1f}s wall clock, stages={meta.get('stage_latency_ms')}")
        return body.get("message_id")

    def greeting_turn(self) -> None:
        """A greeting must be answered as a greeting, not as a query.

        This is the one production check that the router shipped and is
        running. It is separate from the crisis check below and asserts much
        less: no citations, no verse reference in the prose, a short reply, and
        no retrieval stage in the trace. The last of those is the real one -
        citations could be empty because retrieval found nothing, but an absent
        retrieval stage means it never ran.
        """
        self.section("5. a greeting is answered as a greeting")
        status, body, _ = self.request(
            "/api/chat", {"session_id": self.session, "message": GREETING_MESSAGE}
        )
        if not self.check(status == 200, "a greeting returns 200", f"got {status}"):
            print(f"{INFO} {body}")
            return

        assert isinstance(body, dict)
        reply = body.get("reply", "")
        citations = body.get("citations") or []
        words = len(re.findall(r"[^\W\d_]+", reply))

        self.check(body.get("blocked") is False, "a greeting is not blocked")
        self.check(bool(reply.strip()), "the greeting reply is not empty")
        self.check(
            len(citations) == 0,
            "a greeting cites nothing",
            f"got {len(citations)} citation(s): {citations}",
        )
        self.check(
            not re.search(r"\b(?:BG|AS)-\d", reply),
            "no verse reference in the greeting prose",
            "a reference with no citation surface behind it is a dead link",
        )
        self.check(
            words < GREETING_MAX_WORDS,
            f"the greeting is short (< {GREETING_MAX_WORDS} words)",
            f"{words} words",
        )

        stages = (body.get("meta") or {}).get("stage_latency_ms") or {}
        if stages:
            self.check(
                "retrieval" not in stages,
                "retrieval did not run for a greeting",
                f"stages={sorted(stages)}",
            )
            self.check(
                "router" in stages,
                "the router stage ran",
                f"stages={sorted(stages)} - is this build older than Phase 1b?",
            )
        else:
            print(f"{INFO} no stage timings in the response; skipped the stage checks")

    def blocked_turn(self) -> None:
        self.section("6. a crisis turn short-circuits")
        status, body, _ = self.request(
            "/api/chat", {"session_id": self.session, "message": BLOCKED_MESSAGE}
        )
        if not self.check(status == 200, "a blocked turn is still HTTP 200", f"got {status}"):
            return

        assert isinstance(body, dict)
        self.check(body.get("blocked") is True, "the turn is marked blocked")
        self.check(
            body.get("citations") == [],
            "a blocked turn cites nothing",
            "citations here would mean retrieval ran",
        )
        self.check("14416" in body.get("reply", ""), "the crisis text reaches the user")

        stages = (body.get("meta") or {}).get("stage_latency_ms") or {}
        self.check(
            "retrieval" not in stages and "generation" not in stages,
            "no retrieval or generation ran",
            f"stages={sorted(stages)}",
        )

    def feedback(self, message_id: str | None) -> None:
        self.section("7. feedback is recorded")
        if message_id is None:
            self.check(False, "feedback", "no message id from the control turn")
            return
        status, body, _ = self.request(
            "/api/feedback",
            {"message_id": message_id, "rating": "up", "comment": "prod smoke"},
        )
        self.check(status == 200, "a vote is accepted", f"got {status}")
        if isinstance(body, dict):
            self.check(body.get("rating") == "up", "the rating round-trips")

    def rate_limit(self) -> None:
        """Send a genuine burst - concurrently.

        This check used to send its requests one at a time, which cannot trip a
        token bucket when each request takes longer than the refill interval.
        At 5 requests/minute the session bucket regains a token every 12s, and
        production turns take 5-25s, so the bucket was never drained and the
        limiter looked disabled while working perfectly. It passed locally only
        because local turns happened to sit just under 12s.

        Sent together, the burst is a burst. This is also *cheaper* than the
        sequential version: rejected requests are refused before the pipeline
        runs, so only the ones inside the burst capacity spend NIM budget.
        """
        self.section("8. the rate limiter is really on")
        burst = 7
        print(f"{INFO} sending {burst} concurrent requests; only the allowed ones cost NIM")

        results: list[tuple[int, object, dict[str, str]]] = []
        lock = threading.Lock()

        def send(index: int) -> None:
            outcome = self.request(
                "/api/chat", {"session_id": self.session, "message": f"burst {index}"}
            )
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=send, args=(index,)) for index in range(burst)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        statuses = [status for status, _, _ in results]
        limited = [item for item in results if item[0] == 429]
        print(f"{INFO} statuses: {sorted(statuses)}")

        # The detail is only meaningful on failure; printing "none refused" next
        # to a PASS would be the same kind of misleading output this script
        # exists to catch.
        detail = (
            f"{len(limited)}/{burst} refused"
            if limited
            else f"{burst} sent, none refused - the limiter is not protecting the NIM budget"
        )
        if not self.check(bool(limited), "the limiter engages under a concurrent burst", detail):
            return

        _, body, headers = limited[0]
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        self.check(
            retry_after is not None and retry_after.isdigit() and int(retry_after) >= 1,
            "429 carries a usable Retry-After",
            f"Retry-After={retry_after}",
        )
        detail = body.get("detail", {}) if isinstance(body, dict) else {}
        self.check(
            bool(detail.get("message")),
            "429 carries a human-readable message",
            str(detail.get("message"))[:60],
        )

    # ------------------------------------------------------------------ run
    def run(self, skip_rate_limit: bool) -> int:
        print(f"GitaGuide production smoke test\n  target : {self.base}\n  session: {self.session}")
        if self.health() is None:
            print("\nAborting: the service is not answering /health.")
            return 1
        self.self_test_evidence()
        self.consent()
        message_id = self.control_turn()
        self.greeting_turn()
        self.blocked_turn()
        self.feedback(message_id)
        if skip_rate_limit:
            print("\n8. the rate limiter is really on\n" + f"{INFO} skipped (--skip-rate-limit)")
        else:
            self.rate_limit()

        print(f"\n{'-' * 60}")
        if self.failures:
            print(f"FAILED {len(self.failures)}/{self.checks} checks:")
            for name in self.failures:
                print(f"  - {name}")
            print("\nDo not open this deployment to testers until these pass.")
            return 1
        print(f"All {self.checks} checks passed. The deployment is live and behaving.")
        print(f"Smoke session {self.session} is left in the database on purpose,")
        print("so its rows can be told apart from real tester traffic.")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url", help="e.g. https://13-205-163-41.sslip.io")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--skip-rate-limit",
        action="store_true",
        help="skip the burst check (it spends NIM budget)",
    )
    args = parser.parse_args(argv)
    return Smoke(args.base_url, args.timeout).run(args.skip_rate_limit)


if __name__ == "__main__":
    raise SystemExit(main())
