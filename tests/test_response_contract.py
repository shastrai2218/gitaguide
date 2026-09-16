"""The response contract testers asked for on 9 Sep 2026.

Five things, each traceable to a numbered piece of feedback:

* 1 - answers are too long        -> an 80-150 word instruction, released only
                                     by an explicit request for detail
* 3 - combine Gita and Arthashastra -> a cross-source instruction, added only
                                     when the retrieved set really spans both
* 4 - don't print the sloka       -> the prompt forbids quoting, and the
                                     deterministic fallback no longer pastes
                                     verse text either
* 5 - reply in Hinglish           -> the register is mirrored into the prompt
* 6 - make it conversational      -> shape, follow-up question, and an explicit
                                     ban on process narration

What is asserted here is what the code controls: the prompt, the fallbacks and
the recorded register. Whether a live model obeys a word limit is measured
against production, not asserted in a unit test - see kg_measurements.md
section 14.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.llm.fake import FakeLLMProvider
from app.pipeline.generation import (
    BOTH_SOURCES,
    CROSS_SOURCE_INSTRUCTION,
    OVERLOADED_ANSWER,
    SYSTEM_PROMPT,
    GroundedGenerator,
    asks_for_detail,
    build_answer_prompt,
    retrieved_sources,
)
from app.pipeline.register import Register
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.schemas import (
    EmotionResult,
    EmotionScore,
    GraphPath,
    RetrievalTrace,
    RetrievedVerse,
)
from corpus.schemas import ScriptureSource, Verse, extract_verse_ids

GITA = Verse(
    id="BG-2.47",
    source=ScriptureSource.BHAGAVAD_GITA,
    chapter=2,
    verse=47,
    text_english="Your right is to the work itself, never to its fruits.",
    themes=["duty", "detachment"],
)
ARTHA = Verse(
    id="AS-5.2.6",
    source=ScriptureSource.ARTHASHASTRA,
    book=5,
    chapter=2,
    verse=6,
    text_english="They carry on their fraudulent trade while pretending to be honest.",
    themes=["fraud"],
)


def trace_of(*verses: Verse) -> RetrievalTrace:
    return RetrievalTrace(
        query="q",
        results=[
            RetrievedVerse(verse=verse, vector_score=0.9, final_score=0.9,
                           retrieval_channels=["vector"],
                           graph_paths=[GraphPath(description="matched by vector")])
            for verse in verses
        ],
    )


@pytest.fixture
async def live_trace(retriever: InMemoryHybridRetriever) -> RetrievalTrace:
    return await retriever.retrieve("I am anxious about my appraisal")


# Item 1 + 6: the shape and the length
def test_the_system_prompt_states_the_word_budget() -> None:
    assert "80-150 words" in SYSTEM_PROMPT


def test_the_system_prompt_asks_for_takeaway_first_and_a_closing_question() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "take away" in lowered or "takeaway" in lowered
    assert "question" in lowered


@pytest.mark.parametrize(
    "banned",
    ["as an ai", "the user is asking", "the context provides"],
)
def test_the_system_prompt_bans_process_narration(banned: str) -> None:
    """A 7 Sep production answer opened "The user is asking for help from...".

    Not a hypothetical: it is in the traces, from the primary model.
    """
    assert banned in SYSTEM_PROMPT.lower()


def test_the_system_prompt_caps_citations_at_two() -> None:
    assert "AT MOST TWO" in SYSTEM_PROMPT


# Item 1: long form only on request
@pytest.mark.parametrize(
    "query",
    [
        "Can you explain that in more detail?",
        "tell me more about this",
        "I want the full verse please",
        "give me a detailed answer",
        "iska matlab vistar se batao",
    ],
)
def test_explicit_requests_for_detail_are_recognised(query: str) -> None:
    assert asks_for_detail(query)


@pytest.mark.parametrize(
    "query",
    [
        "what should I do?",
        "Can you explain what to do about my manager?",
        "My manager takes credit for my work and I'm getting bitter about it.",
        "A trusted vendor has been overbilling us. How should I handle it?",
        "Is patience a virtue or an excuse?",
    ],
)
def test_ordinary_questions_do_not_unlock_long_form(query: str) -> None:
    """Bare "explain" is how people ask normal questions; it must not count."""
    assert not asks_for_detail(query)


def test_the_length_override_appears_only_when_asked() -> None:
    trace = trace_of(GITA)
    assert "LENGTH OVERRIDE" not in build_answer_prompt("what should I do?", trace)
    assert "LENGTH OVERRIDE" in build_answer_prompt("explain in more detail", trace)


def test_the_length_override_still_forbids_pasting_the_verse() -> None:
    """Longer is licensed; quoting the sloka never is."""
    prompt = build_answer_prompt("explain in more detail", trace_of(GITA))
    override = prompt.split("LENGTH OVERRIDE:")[1].split("\n\n")[0].lower()
    assert "do not paste" in override


# Item 3: cross-source synthesis, only when both are really there
def test_retrieved_sources_reads_the_trace() -> None:
    assert retrieved_sources(trace_of(GITA)) == {ScriptureSource.BHAGAVAD_GITA}
    assert retrieved_sources(trace_of(ARTHA)) == {ScriptureSource.ARTHASHASTRA}
    assert retrieved_sources(trace_of(GITA, ARTHA)) == BOTH_SOURCES
    assert retrieved_sources(RetrievalTrace(query="q", results=[])) == frozenset()


def test_both_sources_present_asks_for_one_woven_answer() -> None:
    prompt = build_answer_prompt("I have to lay off three people", trace_of(GITA, ARTHA))
    assert CROSS_SOURCE_INSTRUCTION in prompt
    assert "weave" in CROSS_SOURCE_INSTRUCTION.lower()
    assert "do not give" in CROSS_SOURCE_INSTRUCTION.lower()


@pytest.mark.parametrize("only", [GITA, ARTHA])
def test_one_source_does_not_ask_for_the_other(only: Verse) -> None:
    """Telling the model to weave two texts when it holds one invites invention."""
    assert CROSS_SOURCE_INSTRUCTION not in build_answer_prompt("q", trace_of(only))


def test_the_cross_source_instruction_assigns_each_text_its_job() -> None:
    lowered = CROSS_SOURCE_INSTRUCTION.lower()
    assert "gita for the inner" in lowered
    assert "arthashastra for the practical" in lowered


# Item 5: the register reaches the prompt and the trace
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("My manager takes credit for my work.", "English"),
        ("mujhe samajh nahi aa raha kya karu", "Hinglish"),
        ("मुझे नौकरी छोड़नी चाहिए?", "Hindi"),
    ],
)
def test_the_prompt_carries_the_users_register(query: str, expected: str) -> None:
    prompt = build_answer_prompt(query, trace_of(GITA))
    assert "REGISTER:" in prompt
    assert f"Reply in {expected}" in prompt


async def test_the_register_is_recorded_on_the_result(
    settings: Settings, live_trace: RetrievalTrace
) -> None:
    """Recorded so register match can be measured, not assumed."""
    generator = GroundedGenerator(FakeLLMProvider(), settings)
    english = await generator.generate("What should I do about my manager?", live_trace)
    hinglish = await generator.generate("mujhe samajh nahi aa raha kya karu", live_trace)

    assert english.answer_register == Register.ENGLISH.value
    assert hinglish.answer_register == Register.HINGLISH.value


def test_the_prompt_never_shows_an_id_the_model_may_not_cite() -> None:
    """The regression the register instruction caused on 9 Sep, pinned here too.

    Every verse id visible in a turn's prompt must be one of the retrieved
    ids, in every register - otherwise the model cites a verse it was never
    given and the turn pays a corrective retry.
    """
    for query in ("what should I do?", "mujhe kya karna chahiye", "मैं क्या करूँ?"):
        trace = trace_of(ARTHA)
        ids = set(extract_verse_ids(build_answer_prompt(query, trace)))
        assert ids <= set(trace.verse_ids), f"{query!r} leaked {ids - set(trace.verse_ids)}"


# Item 4: no verse text in the reply, including from the fallbacks
def test_the_system_prompt_forbids_quoting_the_verse() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "do not quote" in lowered
    assert "sanskrit" in lowered


async def test_the_template_fallback_no_longer_pastes_verse_text(
    settings: Settings, live_trace: RetrievalTrace
) -> None:
    """The deterministic answer used to quote each verse in full.

    It is the answer of last resort, so it must obey the same contract as the
    rest - reference the verses, leave the text to the citation surface - and
    it must stay grounded while doing so.
    """
    stubborn = FakeLLMProvider(
        scripted=[
            "BG-99.99 only, because that is the verse that speaks to this most directly.",
            "BG-98.1 only, for the same reason as before, and nothing else applies here.",
        ]
    )
    result = await GroundedGenerator(stubborn, settings).generate("I am anxious", live_trace)

    assert result.citations, "the fallback must still cite retrieved verses"
    assert set(result.citations) <= set(live_trace.verse_ids)
    for item in live_trace.results:
        text = item.verse.text_english.strip()
        assert text not in result.answer, f"{item.verse_id} was pasted into the reply"


async def test_the_no_context_answer_quotes_nothing(
    llm: FakeLLMProvider, settings: Settings
) -> None:
    empty = RetrievalTrace(query="obscure", results=[])
    result = await GroundedGenerator(llm, settings).generate("obscure", empty)
    assert extract_verse_ids(result.answer) == []
    assert llm.calls == [], "no model call without context"


# The contract must not weaken the guarantees that were already there
async def test_grounding_still_holds_under_the_new_contract(
    llm: FakeLLMProvider, settings: Settings, live_trace: RetrievalTrace
) -> None:
    result = await GroundedGenerator(llm, settings).generate("I am anxious", live_trace)
    assert result.grounded is True
    assert result.citations
    assert set(result.citations) <= set(live_trace.verse_ids)
    assert result.hallucinated_citations == []


async def test_a_blocked_turn_is_untouched_by_any_of_this(
    orchestrator, settings: Settings
) -> None:
    """The gate short-circuits before generation, so no contract applies to it."""
    from app.pipeline.schemas import UserTurn

    result = await orchestrator.handle_turn(UserTurn(message="I want to kill myself"))
    assert result.crisis is True
    assert result.generation is None
    assert result.citations == []


def test_the_emotion_and_intent_framing_survived_the_rewrite() -> None:
    """The prompt still carries the pipeline's own readings."""
    emotion = EmotionResult(labels=[EmotionScore(emotion="annoyance", score=0.7)], intensity=0.5)
    prompt = build_answer_prompt("q", trace_of(GITA), emotion=emotion)
    assert "DETECTED EMOTION: annoyance" in prompt
    assert "DETECTED INTENT:" in prompt
    assert "ALLOWED CITATION IDS: BG-2.47" in prompt


async def test_a_failed_turn_still_records_the_register(settings: Settings) -> None:
    """Found by the 9 Sep measurement run, not by a test.

    A turn that exhausted the generation budget recorded an empty
    answer_register, because only the success path set it. That undercounts
    register match on precisely the turns worth investigating - the register
    was detected fine, it was the answer that never arrived.
    """
    import asyncio

    from app.llm.base import FallbackLLMProvider, LLMProvider, LLMResponse

    class TooSlow(LLMProvider):
        """Answers eventually. The chain's deadline is what fires, not this."""

        name = "slow"
        model = "slow"

        async def complete(self, prompt: str, **kwargs: object) -> LLMResponse:
            await asyncio.sleep(5.0)
            return LLMResponse(text="too late", provider=self.name, model=self.model)

    # A provider that merely *raises* BudgetExhaustedError is not the same
    # thing: FallbackLLMProvider turns that into a plain LLMError, because
    # nothing timed out. Only a real deadline produces the overload turn.
    generator = GroundedGenerator(
        FallbackLLMProvider([TooSlow()], budget_seconds=0.2), settings
    )
    result = await generator.generate("mujhe samajh nahi aa raha kya karu", trace_of(GITA))

    assert result.answer == OVERLOADED_ANSWER
    assert result.answer_register == Register.HINGLISH.value, (
        "the register was detected; only the answer was missing"
    )
