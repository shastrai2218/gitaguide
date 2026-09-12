"""Reply tiers: the per-route contract, and the two ways it could quietly break.

Phase 1 shipped a response contract whose shape and word budget contradicted
each other, and nothing caught it until fourteen real NIM turns had been
generated and counted by hand. The contradiction was cheap to detect and
expensive to find, which is the definition of something that belongs in a test.

So this module asserts two classes of thing:

1. **Each tier agrees with itself** - the budget it states, the shape it asks
   for, and the guard floor it will be judged against are mutually consistent.
2. **The guard cannot shed a compliant reply.** A global floor of 12 words
   would call a correct five-word answer to "thanks" a fragment, retry it, and
   then serve "the guide is overloaded" to someone being polite. That is the
   same failure the bigram threshold caused on 9 Sep, one layer up.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.pipeline.generation import (
    BASE_SYSTEM_PROMPT,
    CITATION_RULES,
    NO_CITATION_RULE,
    assess_answer,
    system_prompt_for,
)
from app.pipeline.router import RETRIEVING_ROUTES, Route
from app.pipeline.tiers import (
    CONVERSATIONAL_RULES,
    GUIDANCE_TIER,
    REPLY_TIERS,
    tier_for,
    tiers_are_self_consistent,
)


def test_every_tier_agrees_with_itself() -> None:
    """The Phase 1 failure, as a test that runs in a second."""
    assert tiers_are_self_consistent() == []


def test_the_guidance_tier_can_actually_reach_the_budget_it_states() -> None:
    """Phase 1's open question, closed.

    The old prompt asked for one sentence, then "two or three", then a closing
    question - five or six sentences, 60-90 words - while stating a budget of
    80-150. The model obeyed the shape and 12 of 14 replies came in under the
    floor. The shape is now three or four connecting sentences, which reaches
    the budget the contract always stated.
    """
    assert GUIDANCE_TIER.min_words == 80
    assert GUIDANCE_TIER.max_words == 150
    assert "three or four" in GUIDANCE_TIER.instruction
    assert "two or three" not in GUIDANCE_TIER.instruction


@pytest.mark.parametrize("route", list(REPLY_TIERS), ids=lambda route: route.value)
def test_a_reply_at_the_bottom_of_its_budget_is_never_shed(route: Route) -> None:
    """The guard must not punish a reply for obeying its own instruction.

    Built from real words rather than a repeated token, because a repeated
    token would trip the stutter check and prove nothing about the floor.
    """
    tier = REPLY_TIERS[route]
    settings = Settings(mock_mode=True, environment="test")
    vocabulary = [
        "take", "care", "and", "come", "back", "whenever", "this", "feels",
        "worth", "turning", "over", "again", "with", "me", "because", "nothing",
        "here", "is", "urgent", "unless", "you", "say", "so", "today", "or",
        "tomorrow", "morning", "either", "way", "the", "choice", "stays",
        "yours", "to", "make", "slowly", "if", "that", "helps", "at", "all",
        "when", "someone", "hands", "back", "what", "they", "were", "given",
        "without", "asking", "for", "more", "than", "was", "offered", "then",
        "little", "remains", "beyond", "doing", "your", "part", "carefully",
        "each", "morning", "before", "anyone", "notices", "or", "thanks",
        "you", "properly", "which", "is", "rarely", "how", "any", "of", "it",
        "actually", "works", "out", "in", "practice", "over", "years", "spent",
        "learning", "patience", "through", "ordinary", "days", "like", "these",
        "ones", "here", "now", "today", "again", "still", "yet", "onward",
        "further", "along", "quietly", "steadily", "somehow", "eventually",
        "afterwards", "regardless", "nonetheless", "meanwhile", "elsewhere",
        "together", "apart", "beside", "beneath", "beyond", "across", "around",
        "between", "during", "against", "toward", "within", "without", "above",
        "below", "behind", "ahead", "nearby", "far", "close", "deep", "wide",
        "long", "short", "early", "late", "soon", "never", "always", "often",
        "rarely", "sometimes", "usually", "perhaps", "certainly", "clearly",
    ]
    assert len(vocabulary) >= tier.min_words
    reply = " ".join(vocabulary[: tier.min_words])
    quality = assess_answer(reply, settings, tier.guard_floor)
    assert not quality.degenerate, f"{route.value}: {quality.summary()}"


def test_the_shortest_honest_acknowledgement_survives() -> None:
    """The concrete case: "thanks" answered in five words.

    At the global floor of 12 this is judged a fragment, retried at the cost of
    a second NIM call, and then shed - so the reply to "thanks" becomes "the
    guide is overloaded". The tier floor is what makes it possible to answer
    briefly on purpose.
    """
    settings = Settings(mock_mode=True, environment="test")
    reply = "Take care of yourself."
    assert assess_answer(reply, settings).degenerate  # global floor: a fragment
    assert not assess_answer(reply, settings, tier_for(Route.ACK_OR_CLOSE).guard_floor).degenerate


def test_word_salad_is_still_salad_at_every_floor() -> None:
    """Lowering the floor for short tiers must not lower the language bar.

    The floor answers "is this long enough to be an answer"; the stutter and
    repetition checks answer "is this language". Only the first is per tier.
    """
    settings = Settings(mock_mode=True, environment="test")
    for tier in REPLY_TIERS.values():
        assert assess_answer("I the I the I the", settings, tier.guard_floor).degenerate
        assert assess_answer("the the the", settings, tier.guard_floor).degenerate


# ------------------------------------------------------------- prompt assembly
@pytest.mark.parametrize("route", list(REPLY_TIERS), ids=lambda route: route.value)
def test_every_tier_prompt_carries_the_identity_and_the_safety_rules(route: Route) -> None:
    """Composed, not copied - so no tier can be written that drops these."""
    prompt = system_prompt_for(REPLY_TIERS[route])
    assert BASE_SYSTEM_PROMPT in prompt
    assert CONVERSATIONAL_RULES in prompt
    assert "therapist, doctor, lawyer or priest" in prompt
    assert "As an AI" in prompt


@pytest.mark.parametrize("route", list(REPLY_TIERS), ids=lambda route: route.value)
def test_citation_rules_appear_only_where_there_are_verses(route: Route) -> None:
    """A greeting prompt that mentions verses invites the model to invent one."""
    tier = REPLY_TIERS[route]
    prompt = system_prompt_for(tier)
    if tier.retrieves:
        assert CITATION_RULES in prompt
        assert NO_CITATION_RULE not in prompt
        assert "AT MOST TWO" in prompt
    else:
        assert NO_CITATION_RULE in prompt
        assert CITATION_RULES not in prompt
        assert "CONTEXT block" not in prompt


@pytest.mark.parametrize("route", list(REPLY_TIERS), ids=lambda route: route.value)
def test_exactly_one_tier_is_described_in_any_prompt(route: Route) -> None:
    """One prompt cannot describe seven behaviours; the blur is the failure.

    Asserted by the instruction blocks being mutually exclusive: no tier's
    prompt may contain another tier's instruction text.
    """
    prompt = system_prompt_for(REPLY_TIERS[route])
    for other_route, other in REPLY_TIERS.items():
        if other_route is route:
            continue
        assert other.instruction not in prompt


@pytest.mark.parametrize("route", list(REPLY_TIERS), ids=lambda route: route.value)
def test_the_budget_is_stated_once_and_unambiguously(route: Route) -> None:
    tier = REPLY_TIERS[route]
    prompt = system_prompt_for(tier)
    assert f"{tier.min_words}-{tier.max_words} words" in prompt
    # Exactly one word range in the whole prompt: two is how Phase 1 happened.
    import re

    ranges = set(re.findall(r"\b\d{1,3}-\d{1,3} words\b", prompt))
    assert ranges == {f"{tier.min_words}-{tier.max_words} words"}, ranges


def test_the_retrieving_tiers_are_exactly_the_retrieving_routes() -> None:
    """One source of truth, asserted from both directions."""
    from_tiers = {route for route, tier in REPLY_TIERS.items() if tier.retrieves}
    assert from_tiers == RETRIEVING_ROUTES


def test_crisis_has_no_tier_and_that_is_deliberate() -> None:
    """The crisis reply is composed by the gate, never by a model or a prompt.

    If a tier existed for it, a routing bug could put a model in front of
    someone in danger. Its absence is the guarantee, so it is asserted rather
    than left to be noticed.
    """
    assert Route.CRISIS not in REPLY_TIERS
    # And asking for it still yields the most complete behaviour, not a crash.
    assert tier_for(Route.CRISIS) is GUIDANCE_TIER


def test_non_retrieving_tiers_cite_nothing() -> None:
    for route, tier in REPLY_TIERS.items():
        if not tier.retrieves:
            assert tier.max_verses == 0, route.value
