"""The output sanity guard: word salad is retried once, then never served.

The fixture below is the production reply of 8 Sep 2026, verbatim, from the
fallback model. It was ``grounded=True`` with three valid citations and a clean
output-safety verdict, because its "Why this verse" section was perfectly
formed. Only the body was noise. Every test here is about telling those apart
without a model: word count, repetition, and stutter over the body alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.llm.fake import FakeLLMProvider
from app.pipeline.generation import (
    OVERLOADED_ANSWER,
    PROVIDER_OVERLOADED,
    STUTTER_BIGRAM_RUN,
    STUTTER_WORD_RUN,
    GroundedGenerator,
    answer_body,
    assess_answer,
)
from app.pipeline.retrieval import InMemoryHybridRetriever
from app.pipeline.safety import CRISIS_RESPONSE_HEADER
from app.pipeline.schemas import GenerationResult, GuidanceResult, RetrievalTrace
from app.telemetry import QUALITY_COUNTERS, Telemetry, set_active_telemetry

#: Message 4002cb8d-3318-4b87-8ac2-2fc248cd1b46, session web-886e1133ba804aa29bc2,
#: nvidia/nemotron-3.5-lightning-30b-a3b, fallback_steps=1. The reply to
#: "My co-founder is quietly building alliances with investors against me."
PRODUCTION_SALAD = """I'll just try a different.

Actually, let you can be the.

I see the the the

I the I the

### Why this verse

- **AS-7.9.7**: This verse was retrieved because your emotion 'confusion' matched its theme of weighing two kinds of friends—one with large but temporary prospects, another longstanding but limited. It surfaces the question of whether a short‑term ally with big upside is worth more than a trusted ally of limited reach, which maps onto your decision about whom to trust with investor overtures.
- **AS-11.1.7**: This verse was retrieved because your emotion 'curiosity' matched its theme of covert operations near sacred sites and strategic deception. Its imagery of feigning agreement to expose hidden agendas maps onto your situation of a co‑founder secretly cultivating investor alliances, offering a workplace‑frame for detecting hidden agendas.
- **AS-11.1.21**: This verse was retrieved because it addresses action and how a leader can endear themselves through virtue and controlled passions. It surfaces the question of whether you can win back trust by modelling the composure and integrity you wish to see, which maps onto your dilemma of responding to a co‑founder’s covert moves."""

#: A short, legitimate production reply (smoke session, "burst 0"), body only.
SHORT_REFUSAL = (
    "I’m sorry, but the verses I have been given do not address the word “burst” or any "
    "related situation. I cannot offer guidance from them on this, and I would rather say "
    "so than improvise scripture at you. If you tell me what is actually on your mind, I "
    "will look again."
)


@pytest.fixture
async def trace(retriever: InMemoryHybridRetriever) -> RetrievalTrace:
    return await retriever.retrieve("I am anxious about my appraisal")


def good_answer(trace: RetrievalTrace) -> str:
    """A coherent, cited answer of the length the prompt asks for."""
    verse = trace.verse_ids[0]
    return (
        f"Take {verse} to heart: the work in front of you is yours, the verdict on it is "
        "not, and that is where your effort belongs today. Name the one conversation you "
        "have been avoiding and have it this week, without deciding in advance how it ends."
    )


@pytest.fixture
def counters(tmp_path: Path):
    """A live telemetry sink, so the counters the guard bumps can be read back."""
    telemetry = Telemetry(
        Settings(
            mock_mode=True,
            telemetry_enabled=True,
            telemetry_path=str(tmp_path / "t.jsonl"),
        )
    )
    set_active_telemetry(telemetry)
    yield telemetry.counters
    set_active_telemetry(None)


# --------------------------------------------------------------------------- #
# The measurement
# --------------------------------------------------------------------------- #
def test_the_production_salad_is_degenerate(settings: Settings) -> None:
    quality = assess_answer(PRODUCTION_SALAD, settings)
    assert quality.degenerate
    assert quality.longest_word_run >= STUTTER_WORD_RUN, "'the the the' is the tell"
    assert quality.longest_bigram_run >= STUTTER_BIGRAM_RUN, "'I the I the' is the other"
    assert any("in a row" in reason for reason in quality.reasons)


def test_the_why_section_cannot_launder_the_body(settings: Settings) -> None:
    """Whole-text statistics said this was fine: 193 words, 112 distinct."""
    body = answer_body(PRODUCTION_SALAD)
    assert "Why this verse" not in body
    assert "AS-7.9.7" not in body

    whole = assess_answer(PRODUCTION_SALAD.replace("### Why this verse", "Why"), settings)
    only_body = assess_answer(PRODUCTION_SALAD, settings)
    assert whole.words > 150 and only_body.words < 30
    assert only_body.degenerate


def test_a_loop_is_degenerate_by_repetition(settings: Settings) -> None:
    quality = assess_answer("I the " * 40, settings)
    assert quality.degenerate
    assert quality.repetition > settings.output_guard_max_repetition


def test_a_fragment_is_degenerate_by_length(settings: Settings) -> None:
    quality = assess_answer("I the I the", settings)
    assert quality.degenerate
    assert quality.words < settings.output_guard_min_words


def test_the_floor_is_twelve_words(settings: Settings) -> None:
    """Documented trade-off: a coherent five-word closer is retried once too."""
    assert settings.output_guard_min_words == 12
    assert assess_answer("You're welcome. Take care.", settings).degenerate
    assert not assess_answer(
        "You're welcome. If something else is weighing on you, tell me and I will look "
        "at the verses again.",
        settings,
    ).degenerate


@pytest.mark.parametrize(
    "text",
    [
        SHORT_REFUSAL,
        OVERLOADED_ANSWER,
        CRISIS_RESPONSE_HEADER,
        # Hinglish in Latin script is words like any other.
        "Aap jo mehsoos kar rahe hain woh samajh mein aata hai. Din bhar dimaag ko "
        "control na kar paana thakaane wala hai, aur BG-6.26 isi baat par hai: jab bhi "
        "mann bhatke, use dheere se wapas lao. Aaj ek chhota kaam chuno aur bas usi par "
        "dhyaan do.",
    ],
)
def test_real_replies_are_clean(text: str, settings: Settings) -> None:
    quality = assess_answer(text, settings)
    assert not quality.degenerate, quality.summary()


async def test_the_fake_provider_is_clean(
    llm: FakeLLMProvider, settings: Settings, trace: RetrievalTrace
) -> None:
    result = await GroundedGenerator(llm, settings).generate("I am anxious", trace)
    assert result.quality_guard == ""
    assert len(llm.calls) == 1


def test_the_stutter_thresholds_are_what_the_docstring_says() -> None:
    assert STUTTER_WORD_RUN == 3
    assert STUTTER_BIGRAM_RUN == 2
    quality = assess_answer(
        "Better to do your own duty imperfectly than another's well, and to do it the the "
        "way you can today rather than the way you imagine you should.",
        Settings(mock_mode=True),
    )
    assert not quality.degenerate, "a single doubled word is a typo, not salad"


# --------------------------------------------------------------------------- #
# The generator
# --------------------------------------------------------------------------- #
async def test_salad_is_retried_once_and_the_retry_served(
    settings: Settings, trace: RetrievalTrace, counters
) -> None:
    good = good_answer(trace)
    llm = FakeLLMProvider(scripted=[PRODUCTION_SALAD, good])

    result = await GroundedGenerator(llm, settings).generate("what should I do", trace)

    assert len(llm.calls) == 2, "exactly one retry"
    assert result.answer.startswith(good.split(":")[0])
    assert result.citations == [trace.verse_ids[0]]
    assert result.quality_guard == "retried"
    assert "degenerate" in result.quality_guard_detail
    assert counters.today()["degenerate_output_detected"] == 1
    assert counters.today()["degenerate_output_recovered"] == 1
    assert counters.today()["degenerate_output_shed"] == 0


async def test_salad_twice_becomes_the_overload_reply_not_the_salad(
    settings: Settings, trace: RetrievalTrace, counters
) -> None:
    llm = FakeLLMProvider(scripted=[PRODUCTION_SALAD, PRODUCTION_SALAD])

    result = await GroundedGenerator(llm, settings).generate("what should I do", trace)

    assert len(llm.calls) == 2, "one retry, not two"
    assert result.answer == OVERLOADED_ANSWER
    assert "the the the" not in result.answer
    assert result.provider == PROVIDER_OVERLOADED
    assert result.citations == [] and result.grounded is False
    assert result.quality_guard == "shed"
    assert "fake" in result.quality_guard_detail, "the detail names the model(s)"
    assert counters.today()["degenerate_output_shed"] == 1


async def test_salad_with_valid_citations_is_still_not_served(
    settings: Settings, trace: RetrievalTrace
) -> None:
    """The exact production shape: noise above a well-formed, correctly cited section."""
    salad_with_real_ids = PRODUCTION_SALAD
    for fake_id, real_id in zip(("AS-7.9.7", "AS-11.1.7", "AS-11.1.21"), trace.verse_ids, strict=False):
        salad_with_real_ids = salad_with_real_ids.replace(fake_id, real_id)
    llm = FakeLLMProvider(scripted=[salad_with_real_ids, salad_with_real_ids])

    result = await GroundedGenerator(llm, settings).generate("what should I do", trace)
    assert result.answer == OVERLOADED_ANSWER
    assert result.quality_guard == "shed"


async def test_the_corrective_rewrite_is_guarded_too(
    settings: Settings, trace: RetrievalTrace
) -> None:
    """Clean but uncited, then salad, then good: three calls, the good one served."""
    uncited = (
        "Name the choice in front of you and take the first small step toward it today, "
        "without deciding in advance how the whole thing must end."
    )
    good = good_answer(trace)
    llm = FakeLLMProvider(scripted=[uncited, PRODUCTION_SALAD, good])

    result = await GroundedGenerator(llm, settings).generate("what should I do", trace)

    assert len(llm.calls) == 3
    assert result.citations == [trace.verse_ids[0]]
    assert result.quality_guard == "retried"


async def test_an_empty_completion_keeps_its_old_path(
    settings: Settings, trace: RetrievalTrace
) -> None:
    """Provider failure is not language; it still falls through to the template."""
    llm = FakeLLMProvider(scripted=["", ""])
    result = await GroundedGenerator(llm, settings).generate("what should I do", trace)
    assert result.citations, "the template answer, as before"
    assert result.quality_guard == ""


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
def test_quality_counters_are_always_present() -> None:
    telemetry = Telemetry(Settings(mock_mode=True, telemetry_enabled=False))
    today = telemetry.counters.today()
    for name in QUALITY_COUNTERS:
        assert today[name] == 0


def test_a_shed_turn_is_not_counted_as_budget_exhaustion(tmp_path: Path) -> None:
    telemetry = Telemetry(
        Settings(mock_mode=True, telemetry_enabled=True, telemetry_path=str(tmp_path / "t.jsonl"))
    )
    shed = GuidanceResult(
        session_id="s",
        message_id="m",
        answer=OVERLOADED_ANSWER,
        generation=GenerationResult(
            answer=OVERLOADED_ANSWER,
            provider=PROVIDER_OVERLOADED,
            grounded=False,
            quality_guard="shed",
            quality_guard_detail="first answer from x: ...",
        ),
    )
    event = telemetry.record_turn(shed, surface="beta")
    assert event is not None and event["quality_guard"] == "shed"
    assert telemetry.counters.today().get("overloaded_turns", 0) == 0

    exhausted = GuidanceResult(
        session_id="s",
        message_id="m2",
        answer=OVERLOADED_ANSWER,
        generation=GenerationResult(
            answer=OVERLOADED_ANSWER, provider=PROVIDER_OVERLOADED, grounded=False
        ),
    )
    telemetry.record_turn(exhausted, surface="beta")
    assert telemetry.counters.today()["overloaded_turns"] == 1
