"""Reply tiers - what each :class:`~app.pipeline.router.Route` is allowed to be.

One prompt cannot describe seven behaviours
-------------------------------------------
The obvious implementation is one system prompt with a table in it: "if this is
a greeting write 15-40 words, if it is guidance write 80-150...". That fails in
a specific and measurable way - the model reads every branch, and the
instructions blur into an average. Phase 1 already produced the evidence: a
single prompt holding both a *shape* ("one sentence, then two or three, then a
question") and a *budget* ("80-150 words") obeyed the shape and missed the
budget on 12 of 14 turns, because the two disagreed and nothing in the prompt
said which won.

So each tier is a **separate, self-consistent block**, and exactly one is
selected per turn. A greeting prompt never contains the word "verse"; a
guidance prompt never mentions greetings. The shape and the budget inside each
tier are checked against each other, in :func:`tiers_are_self_consistent` and
in ``tests/test_tiers.py``, so the Phase 1 contradiction cannot recur silently.

The word budget, resolved
-------------------------
Phase 1 left a question open: the contract said 80-150 words and the model
wrote 65. Neither of the two options offered (widen the shape, or lower the
budget) is taken globally, because the question had no single answer - a
greeting and a layoff dilemma do not share a length. The budget is now **per
tier**, and for ``guidance`` the shape is widened to three or four connecting
sentences so it can actually reach the 80-150 words the contract always stated.

Tiers are prompt-side, not enforcement
--------------------------------------
:attr:`ReplyTier.min_words` and :attr:`max_words` are what the prompt asks for
and what the measurement checks. Only :attr:`guard_floor` is enforced, and only
downward: it is the length below which :func:`app.pipeline.generation.assess_answer`
calls a reply a fragment rather than an answer. It has to be per tier for the
same reason the budget does - a global floor of 12 words would shed a correct
five-word acknowledgement as degenerate, retry it, and serve "the guide is
overloaded" to someone who said "thanks".
"""

from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.router import Route

#: Tone rules that apply to every tier. Kept in one place because they are
#: about being a person rather than about being a particular kind of reply, and
#: because duplicating them into seven blocks is how they drift apart.
CONVERSATIONAL_RULES = """How to talk:
- Reflect their situation in one clause before teaching anything. Not a summary of their \
message back at them - one honest clause that shows you read it.
- Say "you". Contractions are fine. Write the way a thoughtful person talks.
- Never say "As an AI", "Great question", "I understand that you", or "It sounds like you". \
Never narrate what you are doing.
- No bullet lists, no headings, no bold. This is a chat message, not a document.
- End with a question only when it genuinely moves things forward. A question added for \
politeness is filler and reads as one."""


@dataclass(frozen=True)
class ReplyTier:
    """The full specification of one route's reply."""

    route: Route
    #: Does this turn run retrieval at all?
    retrieves: bool
    #: Upper bound on cited verses. Zero means the reply may not cite anything.
    max_verses: int
    min_words: int
    max_words: int
    #: The floor the output sanity guard uses. Always at or below ``min_words``:
    #: the guard catches word salad, not a reply that came in slightly short.
    guard_floor: int
    #: The tier-specific instruction block, self-contained.
    instruction: str

    def budget_line(self) -> str:
        return f"Length: {self.min_words}-{self.max_words} words. This is a hard budget."


GREETING_TIER = ReplyTier(
    route=Route.GREETING,
    retrieves=False,
    max_verses=0,
    min_words=15,
    max_words=40,
    guard_floor=8,
    instruction="""They are saying hello and have not asked anything yet.

Say hello back, warmly, in their own language and register. Add one plain line about what \
you can actually help with - a decision they are stuck on, a situation at work or at home, \
something they cannot stop turning over. Then ask what brought them here.

Do not cite a verse. Do not quote Sanskrit. Do not teach anything yet. They have not told \
you a single thing about themselves, so anything you teach is a guess dressed as wisdom.""",
)

META_TIER = ReplyTier(
    route=Route.META,
    retrieves=False,
    max_verses=0,
    min_words=30,
    max_words=80,
    guard_floor=15,
    instruction="""They are asking about you, not about their life.

Answer honestly and plainly. You are an AI guidance companion; you are not a person, a \
therapist, a priest or a guru, and you should say so without apologising for it. What you \
do is find passages in the Bhagavad Gita and Kautilya's Arthashastra that bear on someone's \
situation, and talk through what they suggest.

If they asked which edition: the Bhagavad Gita text is the Purohit Swami translation, which \
is in the public domain, and every reference links to vedabase.io so they can read the verse \
and other translations for themselves. If they asked whether the verses can be trusted: every \
verse shown is retrieved from that text and shown with its reference, so they can check any \
of it - and say plainly that you can be wrong about which verse fits, which is exactly why \
the reference is there.

Do not cite a verse id in this reply and do not teach. Answer the question they asked, then \
offer to help with something real.""",
)

ACK_TIER = ReplyTier(
    route=Route.ACK_OR_CLOSE,
    retrieves=False,
    max_verses=0,
    min_words=5,
    max_words=25,
    guard_floor=3,
    instruction="""They are acknowledging what you said, or closing the conversation.

Mirror their tone and length. If they are closing, wish them well and stop. If they are \
acknowledging, leave the door open in one short line.

Nothing else. No new teaching, no summary of what you already said, and no question unless \
they left something visibly unfinished. Do not cite a verse - they did not ask a question, so \
there is nothing to answer with one. The single most common way to get this wrong is to treat \
"ok" as an invitation to keep going.""",
)

FOLLOWUP_TIER = ReplyTier(
    route=Route.FOLLOWUP,
    retrieves=True,
    max_verses=2,
    min_words=40,
    max_words=110,
    guard_floor=12,
    instruction="""They are continuing the exchange you are already in.

Continue the thought - do not restart it. They have already heard your opening; repeating it \
in new words is the thing that makes a conversation feel like a form. Pick up where you left \
off and add what they asked for: the example, the simpler version, the answer to the \
objection they just raised.

Prefer the verses already discussed in this conversation. Reach for a new one only when what \
they are now asking genuinely is not covered by it, and cite at most two in total.""",
)

GUIDANCE_TIER = ReplyTier(
    route=Route.GUIDANCE,
    retrieves=True,
    max_verses=2,
    min_words=80,
    max_words=150,
    guard_floor=12,
    instruction="""They have brought you a real situation and want help with it.

Shape:
- Open with the single thing you would most want them to take away, in one sentence.
- Then three or four sentences connecting it to what they actually described - what the \
verse is asking of them here, in their situation, in concrete terms. Name at most two verses \
by id.
- Close with one short question that would move things forward, when a question is natural.

Do NOT quote, paste or transliterate the verse text, and do not include Sanskrit. The reader \
is shown each verse's reference and full text separately, so repeating it here makes the \
answer twice as long and no more useful. Say what the verse means in your own words.""",
)

OUT_OF_SCOPE_TIER = ReplyTier(
    route=Route.OUT_OF_SCOPE,
    retrieves=False,
    max_verses=0,
    min_words=20,
    max_words=50,
    guard_floor=10,
    instruction="""They have asked for something you do not do.

Say so plainly and without embarrassment - you are not a search engine, a translator or a \
coding assistant. Then say in one line what you are for: working through a decision, a \
conflict or something they cannot settle, using the Gita and the Arthashastra.

Be friendly about it. Do not lecture them about scope, and do not cite a verse - stretching \
to answer anyway with one that is loosely about their topic is how a guidance tool becomes a \
horoscope.""",
)

#: ``crisis`` has no tier. The safety gate composes that reply itself and the
#: generator is never reached, which is the only arrangement in which the crisis
#: text cannot be influenced by a model, a prompt or a routing decision. Its
#: absence here is load-bearing, not an omission.
REPLY_TIERS: dict[Route, ReplyTier] = {
    tier.route: tier
    for tier in (
        GREETING_TIER,
        META_TIER,
        ACK_TIER,
        FOLLOWUP_TIER,
        GUIDANCE_TIER,
        OUT_OF_SCOPE_TIER,
    )
}


def tier_for(route: Route) -> ReplyTier:
    """The tier for a route, defaulting to guidance.

    Guidance is the default for the same reason it is the router's fallback: it
    is the most complete behaviour, so an unmapped route degrades to answering
    the question properly rather than to brushing it off.

    >>> tier_for(Route.GREETING).max_verses
    0
    >>> tier_for(Route.CRISIS) is GUIDANCE_TIER
    True
    """
    return REPLY_TIERS.get(route, GUIDANCE_TIER)


def tiers_are_self_consistent() -> list[str]:
    """Complaints about any tier whose own numbers disagree.

    This exists because of a measured failure, not as decoration. The Phase 1
    contract asked for 80-150 words in a shape that could only produce 60-90,
    and nothing caught it until fourteen real turns had been generated and
    counted. A sentence range implies a word range, so the two can be checked
    against each other cheaply and continuously.

    Sentences are counted from the tier's own prose; the bound assumes an
    ordinary written sentence runs 12-28 words.
    """
    complaints: list[str] = []
    for route, tier in REPLY_TIERS.items():
        if tier.min_words > tier.max_words:
            complaints.append(f"{route.value}: min_words above max_words")
        if tier.guard_floor > tier.min_words:
            complaints.append(
                f"{route.value}: guard floor {tier.guard_floor} above the "
                f"asked-for minimum {tier.min_words}, so a compliant reply would be shed"
            )
        if tier.max_verses == 0 and "cite" not in tier.instruction.lower():
            complaints.append(
                f"{route.value}: cites nothing but never says so in the instruction"
            )
        if tier.retrieves != (tier.max_verses > 0):
            complaints.append(
                f"{route.value}: retrieves={tier.retrieves} but max_verses={tier.max_verses}"
            )
    return complaints
