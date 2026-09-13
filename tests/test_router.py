"""The conversational router: what it decides, what it refuses to decide, and
what happens when it breaks.

Read the accuracy claims here carefully, because two very different things are
being measured and only one of them is a result:

* the **stage-1 lexicon** tests are a real measurement. No model is involved,
  the answer is deterministic, and the golden set is the same
  ``eval/conversation_set.jsonl`` the Phase 1b numbers come from.
* the **end-to-end offline** test is a *wiring* test. Stage 2 runs on
  :class:`app.llm.fake.FakeLLMProvider`, whose routing is a keyword heuristic
  written in this repository. It proves the two stages compose, the JSON is
  parsed and the enum is honoured. It is NOT router accuracy and must never be
  quoted as such - that number comes from the real cheap tier, in
  ``docs/kg_measurements.md`` section 14.

The tests that matter most are neither: they are the ones below the line, which
assert that a router which times out, crashes, babbles or lies still leaves the
user with a real answer, and that nothing here can touch a crisis turn.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings
from app.llm.base import LLMError, LLMProvider, LLMResponse
from app.llm.fake import FakeLLMProvider
from app.pipeline.memory import MemoryContext
from app.pipeline.router import (
    DEFAULT_ROUTE,
    LEXICON_PRECEDENCE,
    LEXICON_ROUTES,
    MODEL_ROUTES,
    RETRIEVING_ROUTES,
    SHORT_MESSAGE_WORDS,
    ConversationRouter,
    Lexicon,
    Route,
    RouterDecisionStage,
    _normalise,
    build_route_prompt,
    rule_route,
)

CONVERSATION_SET = PROJECT_ROOT / "eval" / "conversation_set.jsonl"


def load_set() -> list[dict]:
    return [
        json.loads(line)
        for line in CONVERSATION_SET.read_text(encoding="utf-8").splitlines()
        if line.strip() and '"_comment"' not in line[:20]
    ]


GOLDEN = load_set()


def memory_with(history: list[list[str]]) -> MemoryContext:
    return MemoryContext(
        session_id="s",
        recent_turns=[(role, text) for role, text in history],
        turn_count=len(history) // 2,
    )


# --------------------------------------------------------------------- the set
def test_the_conversation_set_is_the_shape_the_measurement_assumes() -> None:
    """40 messages, and the non-guidance count the Step 0 finding is stated in."""
    assert len(GOLDEN) == 40
    counts: dict[str, int] = {}
    for item in GOLDEN:
        counts[item["expected_route"]] = counts.get(item["expected_route"], 0) + 1
    assert counts == {
        "greeting": 8,
        "meta": 6,
        "ack_or_close": 6,
        "followup": 6,
        "guidance": 14,
    }
    # "20/20 non-guidance messages got a verse they didn't ask for" is only a
    # meaningful sentence while this holds.
    never_retrieve = counts["greeting"] + counts["meta"] + counts["ack_or_close"]
    assert never_retrieve == 20


def test_every_followup_case_carries_the_history_it_depends_on() -> None:
    """A follow-up judged without its history is being judged on another task."""
    for item in GOLDEN:
        if item["expected_route"] == "followup":
            assert item["history"], f"{item['id']} has no history"


# ----------------------------------------------------------- stage 1 (lexicon)
@pytest.mark.parametrize(
    "item",
    [item for item in GOLDEN if item["expected_route"] in {"greeting", "ack_or_close"}],
    ids=lambda item: item["id"],
)
def test_stage_one_decides_the_whole_zero_cost_bucket(item: dict) -> None:
    """Every greeting and acknowledgement, in four languages, with no model.

    This is the half of the router that costs nothing, and it is expected to be
    exhaustive over these two buckets: if one falls through, the system pays a
    model call to answer "ok".
    """
    decision = rule_route(item["message"])
    assert decision is not None, f"{item['id']} fell through to the model"
    assert decision.route.value == item["expected_route"]
    assert decision.stage is RouterDecisionStage.RULES


@pytest.mark.parametrize(
    "item",
    [item for item in GOLDEN if item["expected_route"] == "guidance"],
    ids=lambda item: item["id"],
)
def test_stage_one_never_decides_a_real_question(item: dict) -> None:
    """The invariant that survives the lexicon growing.

    Stage 1 was extended on 13 Sep to decide `meta` and `followup` as well,
    because those were the largest source of routing error at a 4-second
    ceiling and three of the five misses were timeouts rather than
    misjudgements. What may **never** change is this: a message that is
    actually someone asking for help must not be decided by a word list.
    Greeting, acknowledgement, meta and follow-up are all messages with no
    subject of their own, which is what a word list can recognise; guidance is
    not, and is only ever reached through the model or by failing open.
    """
    assert rule_route(item["message"]) is None


# ------------------------------------------------------- stage 1 safety rules
@pytest.mark.parametrize(
    "message",
    [
        "hi i want to die",
        "hello i cannot go on",
        "ok i am going to end it",
        "thanks but i want to kill myself",
        "namaste mujhe jeene ka mann nahi",
        "bye cruel world i am done",
    ],
)
def test_a_greeting_word_never_swallows_the_message_after_it(message: str) -> None:
    """Containment matching would be a safety bug. Whole-message or nothing.

    Every one of these opens with a lexicon term, and none may be decided by
    stage 1: they must fall through to the model, and a model failure falls
    through to guidance, which retrieves and answers. The safety gate is what
    actually catches these - this test is about never letting the cheap path
    reach them first.
    """
    assert rule_route(message) is None


def test_stage_one_is_bounded_by_length_as_well_as_content() -> None:
    """Even an all-lexicon message stops being a greeting once it is long."""
    bound = Lexicon.load().max_message_words
    assert rule_route(" ".join(["ok"] * (bound + 1))) is None


def test_the_length_bound_tracks_the_longest_phrase_and_no_further() -> None:
    """Adding "can i trust these verses" must not widen the gate for "ok".

    The bound is the longest phrase in the file, so a five-word phrase permits
    a five-word message and nothing more. It is checked here because the
    alternative - a hand-maintained constant - drifts the moment someone adds a
    longer phrase, and the failure mode is silent over-permission.
    """
    lexicon = Lexicon.load()
    longest = max(len(phrase.split()) for phrase in lexicon.phrases)
    assert lexicon.max_message_words == max(SHORT_MESSAGE_WORDS, longest)
    assert lexicon.max_message_words <= 6, "the cheap path is getting too permissive"


@pytest.mark.parametrize(
    "message",
    ["नमस्ते", "ठीक है", "क्या हाल है", "समझ गया", "धन्यवाद", "अलविदा"],
)
def test_devanagari_is_matched_as_words_not_as_stripped_consonants(
    message: str,
) -> None:
    """A regression test for a bug this file's own word count exposed.

    ``\\w`` matches what ``str.isalnum()`` accepts, and a Devanagari matra is
    category Mn, which is not alphanumeric - so the original normaliser turned
    "नमस्ते" into "न म स त". Matching still worked, because the lexicon was
    mangled identically, but the word count did not: a five-word Hindi phrase
    counted as seven and silently widened the stage-1 length gate for every
    message in every language. Five phrases also collided with each other once
    their vowel signs were removed.
    """
    decision = rule_route(message)
    assert decision is not None, message
    assert len(_normalise(message).split()) <= 3, _normalise(message)


def test_stage_one_tolerates_punctuation_emoji_and_stretched_letters() -> None:
    for message in ("Hi!!!", "hiiii", "namaste 🙏", "OK.", "thanks!!", "okkk"):
        assert rule_route(message) is not None, message


def test_a_missing_lexicon_disables_stage_one_rather_than_failing() -> None:
    """Losing a convenience must not take the service down."""
    assert rule_route("hi", Lexicon.empty()) is None


def test_the_lexicon_file_holds_no_crisis_vocabulary() -> None:
    """Nothing in the zero-cost path may be able to decide a cry for help."""
    lexicon = Lexicon.load()
    forbidden = {
        "die", "death", "kill", "end", "suicide", "harm", "hurt", "alone",
        "help", "marna", "jeena", "khatam", "dukh", "pareshan",
    }
    for phrase in lexicon.phrases:
        for word in phrase.split():
            assert word not in forbidden, f"{word!r} is stage-1 decidable"
    # And the file may only ever decide the subjectless routes. A lexicon that
    # could return `guidance` would be deciding, at zero cost and with no
    # model, that a message IS a real question - a much stronger claim than
    # deciding it is a greeting, and not one a word list can support.
    assert set(lexicon.phrases.values()) <= LEXICON_ROUTES
    assert Route.GUIDANCE not in LEXICON_ROUTES
    assert Route.CRISIS not in LEXICON_ROUTES


def test_a_lexicon_naming_a_forbidden_route_is_refused_not_ignored(
    tmp_path: Path,
) -> None:
    """Silently dropping the section would leave the author believing it worked."""
    bad = tmp_path / "lexicon.yaml"
    bad.write_text(
        "fillers: []\nroutes:\n  guidance:\n    english:\n      - what should i do\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="may not be decided"):
        Lexicon.load(bad)


def test_every_lexicon_route_can_be_resolved_by_the_precedence_order() -> None:
    """A route missing from the precedence tuple falls off the end of the search."""
    assert set(LEXICON_PRECEDENCE) == LEXICON_ROUTES


def test_a_request_beats_social_framing_when_both_match() -> None:
    """"hi who are you" is a question with a greeting attached."""
    assert rule_route("hi who are you").route is Route.META
    # And between the two purely social routes the opening move still wins.
    assert rule_route("hi thanks").route is Route.GREETING


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("who are you", Route.META),
        ("tum kaun ho", Route.META),
        ("kya kar sakte ho", Route.META),
        ("can I trust these verses", Route.META),
        ("तुम कौन हो", Route.META),
        ("is this AI", Route.META),
        ("in Hindi", Route.FOLLOWUP),
        ("hindi me", Route.FOLLOWUP),
        ("example do", Route.FOLLOWUP),
        ("simpler", Route.FOLLOWUP),
        ("aur kya", Route.FOLLOWUP),
        ("और क्या", Route.FOLLOWUP),
    ],
)
def test_the_phrases_that_kept_losing_to_the_deadline_now_cost_nothing(
    message: str, expected: Route
) -> None:
    """The 13 Sep extension, named case by case.

    At a four-second ceiling these were where routing actually failed, and
    three of the five misses were the call being cancelled rather than the
    model being wrong - it knew the answer and ran out of time. They are fixed
    phrases, so a word list answers them at zero cost and the deadline stops
    applying to them at all.
    """
    decision = rule_route(message)
    assert decision is not None, f"{message!r} still costs a model call"
    assert decision.route is expected


# ---------------------------------------------------------- the enum contract
def test_the_model_may_not_choose_crisis() -> None:
    """Crisis is the gate's verdict, never a language model's opinion."""
    assert Route.CRISIS not in MODEL_ROUTES
    assert Route.CRISIS not in RETRIEVING_ROUTES


def test_only_guidance_and_followup_retrieve() -> None:
    assert {Route.GUIDANCE, Route.FOLLOWUP} == RETRIEVING_ROUTES


def test_the_default_is_the_most_complete_behaviour_not_the_cheapest() -> None:
    """A broken router must degrade to answering, not to brushing off."""
    assert DEFAULT_ROUTE is Route.GUIDANCE
    assert DEFAULT_ROUTE in RETRIEVING_ROUTES


def test_the_prompt_names_every_route_the_model_may_pick() -> None:
    prompt = build_route_prompt("hello")
    for route in MODEL_ROUTES:
        assert route.value in prompt
    assert "crisis" not in prompt.lower()


def test_the_prompt_says_plainly_when_there_is_no_history() -> None:
    """A model told nothing about history invents a previous turn to follow."""
    assert "none - this is the first message" in build_route_prompt("aur kya")
    with_history = build_route_prompt("aur kya", memory_with([["user", "I am angry"]]))
    assert "I am angry" in with_history


# ------------------------------------------------------------ end-to-end (fake)
@pytest.mark.parametrize("item", GOLDEN, ids=lambda item: item["id"])
async def test_router_wiring_end_to_end_offline(item: dict) -> None:
    """WIRING, not accuracy - see this module's docstring.

    Stage 2 here is the fake provider's keyword heuristic, written in this
    repository. What this proves is that stage 1 hands over correctly, the JSON
    round-trips, the label becomes a ``Route``, and a follow-up without history
    is not accepted as one.
    """
    router = ConversationRouter(llm=FakeLLMProvider())
    decision = await router.route(item["message"], memory_with(item["history"]))
    assert decision.route.value == item["expected_route"]


async def test_a_followup_without_history_is_treated_as_guidance() -> None:
    """The model produces this on first turns; it is a contradiction in terms."""

    class SaysFollowup(FakeLLMProvider):
        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            return LLMResponse(
                text=json.dumps({"route": "followup", "standalone_query": "more"}),
                provider="fake",
                model="test",
            )

    decision = await ConversationRouter(llm=SaysFollowup()).route("aur kya", None)
    assert decision.route is Route.GUIDANCE


async def test_a_model_routed_followup_retrieves_on_its_rewrite() -> None:
    """A follow-up's own words have no retrievable signal; the rewrite does.

    Uses a phrase the lexicon does **not** hold, so this exercises the model
    path. The lexicon path is the test below, and the two now behave
    differently - see it for why that matters.
    """

    class Rewrites(FakeLLMProvider):
        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            return LLMResponse(
                text=json.dumps(
                    {
                        "route": "followup",
                        "standalone_query": (
                            "further guidance on controlling anger after being "
                            "scolded by a manager in front of colleagues"
                        ),
                    }
                ),
                provider="fake",
                model="test",
            )

    message = "but what if he does not listen to any of that"
    assert rule_route(message) is None, "this test needs the model path"
    memory = memory_with([["user", "boss ne daanta"], ["assistant", "BG-2.62"]])
    decision = await ConversationRouter(llm=Rewrites()).route(message, memory)
    assert decision.route is Route.FOLLOWUP
    assert decision.retrieval_query(message, memory).startswith("further guidance")


async def test_a_lexicon_routed_followup_retrieves_on_the_previous_message() -> None:
    """The zero-cost path buys latency and gives up the rewrite.

    "aur kya" is now decided by the lexicon, so no model runs and there is no
    standalone query to search for. Sending "aur kya" to the retriever would
    match nothing, so the person's previous message stands in - it is the thing
    being followed up on.

    **This is a real trade and it is recorded rather than hidden.** For an
    English session it costs nothing. For a Hinglish one it means the vector leg
    searches raw Hinglish, which is measured at 0.00 against an English corpus
    (kg_measurements section 14b), where a model rewrite would have been
    English. Deciding follow-ups at zero cost and rewriting them for retrieval
    are in tension, and this test exists so the tension is visible to whoever
    changes it next.
    """
    memory = memory_with(
        [["user", "boss ne sabke saamne daanta"], ["assistant", "Anger (BG-2.62)."]]
    )
    decision = await ConversationRouter(llm=FakeLLMProvider()).route("aur kya", memory)
    assert decision.route is Route.FOLLOWUP
    assert decision.stage is RouterDecisionStage.RULES
    assert decision.standalone_query == ""
    assert decision.retrieval_query("aur kya", memory) == "boss ne sabke saamne daanta"
    # And with no history at all it is not a follow-up in the first place.
    first_turn = await ConversationRouter(llm=FakeLLMProvider()).route("aur kya", None)
    assert first_turn.route is Route.GUIDANCE


async def test_a_guidance_turn_retrieves_on_the_persons_own_words() -> None:
    """Rewriting a standalone question can only lose the person's vocabulary."""
    decision = await ConversationRouter(llm=FakeLLMProvider()).route(
        "mera boss mujhe pressure deta hai", None
    )
    assert decision.route is Route.GUIDANCE
    assert decision.retrieval_query("mera boss mujhe pressure deta hai") == (
        "mera boss mujhe pressure deta hai"
    )


def test_routes_that_do_not_retrieve_carry_no_standalone_query() -> None:
    """A query nothing will search for is a field that can only mislead."""

    async def check() -> None:
        router = ConversationRouter(llm=FakeLLMProvider())
        decision = await router.route("who are you", None)
        assert decision.route is Route.META
        assert decision.standalone_query == ""

    asyncio.run(check())


# --------------------------------------------------------------- failing open
class _Broken(LLMProvider):
    """A provider that fails in one specific way."""

    name = "broken"
    model = "broken-v1"

    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def complete(self, prompt, **kwargs):  # type: ignore[override]
        if self.mode == "timeout":
            await asyncio.sleep(30)
        if self.mode == "raise":
            raise LLMError("NIM is down")
        if self.mode == "prose":
            return LLMResponse(text="Sure! I think this is a greeting.", provider="broken")
        if self.mode == "unknown_label":
            return LLMResponse(text=json.dumps({"route": "banter"}), provider="broken")
        if self.mode == "reserved_label":
            return LLMResponse(text=json.dumps({"route": "crisis"}), provider="broken")
        if self.mode == "empty":
            return LLMResponse(text="", provider="broken")
        raise AssertionError(self.mode)

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


@pytest.mark.parametrize(
    "mode", ["timeout", "raise", "prose", "unknown_label", "reserved_label", "empty"]
)
async def test_every_router_failure_still_answers_the_question(mode: str) -> None:
    """The failure mode of the router is "behaves like it did before Phase 1b".

    Not "apologises", not "greets", not "500". Guidance retrieves and answers,
    which is what every message got before routing existed, so the worst case
    of adding a router is the behaviour testers already had.
    """
    settings = Settings(mock_mode=True, environment="test", router_timeout_seconds=0.2)
    router = ConversationRouter(llm=_Broken(mode), settings=settings)
    decision = await router.route("my manager took credit for my work", None)
    assert decision.route is Route.GUIDANCE
    assert decision.retrieves
    assert decision.stage is RouterDecisionStage.FALLBACK
    assert decision.fell_back
    assert decision.rationale, "a fallback with no reason cannot be debugged"


async def test_the_router_never_swallows_cancellation() -> None:
    """``CancelledError`` is a ``BaseException``; catching it orphans the turn.

    This is the same defect Phase 3 fixed in the request path, and a bare
    ``except Exception`` here would have quietly reintroduced it one stage
    earlier.
    """

    class Cancels(LLMProvider):
        name = "cancels"
        model = "x"

        async def complete(self, prompt, **kwargs):  # type: ignore[override]
            raise asyncio.CancelledError

        async def aclose(self) -> None:  # pragma: no cover
            return None

    with pytest.raises(asyncio.CancelledError):
        await ConversationRouter(llm=Cancels()).route("anything at all here", None)


async def test_a_router_timeout_costs_latency_not_an_answer() -> None:
    settings = Settings(mock_mode=True, environment="test", router_timeout_seconds=0.15)

    async def run() -> None:
        decision = await ConversationRouter(
            llm=_Broken("timeout"), settings=settings
        ).route("should I take the promotion", None)
        assert decision.route is Route.GUIDANCE

    await asyncio.wait_for(run(), timeout=5.0)


async def test_a_fallback_is_counted() -> None:
    """``intent_router_fallback`` is how a degraded router is noticed at all.

    Without the counter a router that has silently fallen back on every turn
    for a week is indistinguishable from one that is working - both answer the
    question, one just answers "hi" with a verse.
    """
    from app import telemetry

    sink = telemetry.Telemetry(Settings(mock_mode=True, environment="test"))
    telemetry.set_active_telemetry(sink)
    try:
        settings = Settings(mock_mode=True, environment="test", router_timeout_seconds=0.1)
        await ConversationRouter(llm=_Broken("raise"), settings=settings).route(
            "my team keeps missing deadlines", None
        )
        assert sink.counters.today()["intent_router_fallback"] == 1
    finally:
        telemetry.set_active_telemetry(None)


async def test_no_router_model_means_everything_is_guidance() -> None:
    """``router_model_enabled=false`` is the one-env-var way back."""
    router = ConversationRouter(llm=None)
    assert (await router.route("hi")).route is Route.GREETING  # stage 1 still works
    decision = await router.route("my manager took credit for my work")
    assert decision.route is Route.GUIDANCE
    assert decision.stage is RouterDecisionStage.FALLBACK
