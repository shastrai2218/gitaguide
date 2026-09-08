"""Golden cases for register detection, in all three registers.

The asymmetry that shapes these tests: answering an English speaker in Hinglish
is a worse error than answering a Hinglish speaker in English, because the
first looks broken and the second merely looks stiff. So the English battery is
the long one, and it includes every English prompt from the Day 7 acceptance
run and the tester prompt set - if any of those classify as Hinglish, the
marker list is too greedy.
"""

from __future__ import annotations

import pytest

from app.pipeline.register import (
    MARKERS,
    Register,
    detect_register,
    register_instruction,
)

# --------------------------------------------------------------------------- #
# Hindi (Devanagari)
# --------------------------------------------------------------------------- #
HINDI = [
    "मुझे नौकरी छोड़नी चाहिए?",
    "मेरा मन नहीं लग रहा है आजकल",
    "मेरे बॉस मेरा काम अपने नाम पर ले लेते हैं। मैं क्या करूँ?",
    "गीता कर्म के बारे में क्या कहती है?",
    "मैं बहुत परेशान हूँ और कुछ समझ नहीं आ रहा",
]

# --------------------------------------------------------------------------- #
# Hinglish (Roman-script Hindi, and Romanised Gujarati by documented decision)
# --------------------------------------------------------------------------- #
HINGLISH = [
    # From real production traffic, 7 Sep (session web-886e1133…).
    "muje din bhar aacha nahi laag raha mein apne dimaag ko control nahi kar paa raha hu",
    "mujhe ab jeene ka mann nahi karta",
    # From the tester prompt set.
    "Mera boss mujhse bahut kaam karwata hai aur credit khud le leta hai. Main kya karoon?",
    # Short messages that must not need two markers.
    "kya karu?",
    "mujhe samajh nahi aa raha",
    # Code-mixed the way people actually write it.
    "mera manager mujhe bahut pressure deta hai, kya main resign kar du?",
    "aaj bahut tension hai office mein, kuch samajh nahi aa raha",
    # Romanised Gujarati - tester B's own words.
    "emne answers vague lagya",
    "dataset mate prabhupada's bhagwad gita use krvani kidhi",
]

# --------------------------------------------------------------------------- #
# English - the false-positive battery
# --------------------------------------------------------------------------- #
ENGLISH = [
    # Day 7 acceptance turns, verbatim.
    "My manager takes credit for my work and I'm getting bitter about it.",
    "A trusted vendor has been overbilling us. How should I handle it?",
    "Is patience a virtue or an excuse?",
    "I think everyone would be better off without me.",
    "I keep putting off a decision about my career and it is eating at me.",
    # Tester prompt set, English entries.
    "I'm writing a paper on how the Gita treats death — what does Krishna say about it?",
    "My grandfather passed away last month and I'm struggling to focus at work",
    "I never said I wanted to give up, I just feel exhausted by this project",
    "What does the Arthashastra say about punishing a corrupt official?",
    "I feel like quitting my job — not life, just this job",
    "I've been offered a promotion but I'm terrified of failing in front of everyone",
    "I keep procrastinating on things I care about and then hating myself for it",
    "How do I stay calm when my team keeps missing deadlines I'm accountable for?",
    "My co-founder is quietly building alliances with investors against me. How should I respond?",
    "I'm a new team lead and two senior people openly ignore my decisions",
    "A competitor is poaching my best engineers with higher offers I can't match",
    "I need to negotiate with a client who has all the leverage",
    "I have to lay off three people next week and I can't sleep",
    "Should I report my friend at work who's cutting corners on safety?",
    # Sentences built specifically from the English words nearest the marker
    # list, to prove the exclusions hold.
    "The main problem is that my par for the course is slipping.",
    "My soul mate and I log every issue and the din in the office is unbearable.",
    "That was the plan, and the cab is here.",
    "I want to be the person who does the work, not the one who takes the credit.",
    "Can you tell me what the text actually says here?",
]


@pytest.mark.parametrize("text", HINDI, ids=lambda t: t[:28])
def test_devanagari_is_hindi(text: str) -> None:
    assert detect_register(text).register is Register.HINDI


@pytest.mark.parametrize("text", HINGLISH, ids=lambda t: t[:34])
def test_roman_script_indic_is_hinglish(text: str) -> None:
    result = detect_register(text)
    assert result.register is Register.HINGLISH, result.summary()


@pytest.mark.parametrize("text", ENGLISH, ids=lambda t: t[:34])
def test_english_is_never_mistaken_for_hinglish(text: str) -> None:
    """The expensive direction of the error, so it gets the biggest battery."""
    result = detect_register(text)
    assert result.register is Register.ENGLISH, result.summary()


# --------------------------------------------------------------------------- #
# The boundaries themselves
# --------------------------------------------------------------------------- #
def test_one_quoted_sanskrit_word_does_not_make_an_english_question_hindi() -> None:
    """An English question *about* a Hindi word is an English message."""
    result = detect_register("What does धर्म actually mean in chapter 2?")
    assert result.register is Register.ENGLISH
    assert 0 < result.devanagari_ratio < 0.20


def test_a_mostly_devanagari_message_with_english_words_is_hindi() -> None:
    result = detect_register("मेरे manager ने मेरा credit ले लिया, मैं क्या करूँ?")
    assert result.register is Register.HINDI


def test_a_single_marker_in_a_long_english_sentence_is_not_enough() -> None:
    """One marker plus many English words stays English."""
    result = detect_register(
        "I would like to know whether the promotion is a good idea for my career and my family"
    )
    assert result.register is Register.ENGLISH


def test_two_markers_are_enough() -> None:
    result = detect_register("mujhe lagta hai this is a bad decision")
    assert result.register is Register.HINGLISH
    assert len(result.markers) >= 2


def test_empty_and_punctuation_only_are_english() -> None:
    for text in ("", "   ", "?", "...", "123"):
        assert detect_register(text).register is Register.ENGLISH


def test_the_result_carries_its_evidence() -> None:
    result = detect_register("mujhe samajh nahi aa raha kya karu")
    assert result.words > 0
    assert result.markers, "the markers that decided it must be reported"
    assert "hinglish" in result.summary()


# --------------------------------------------------------------------------- #
# The marker list's own hygiene
# --------------------------------------------------------------------------- #
def test_no_marker_is_shorter_than_three_letters() -> None:
    """Two-letter Hindi words collide with typos and abbreviations."""
    assert not [marker for marker in MARKERS if len(marker) < 3]


@pytest.mark.parametrize(
    "word",
    ["main", "par", "mate", "the", "that", "kam", "cab", "din", "log", "is", "so", "to", "he"],
)
def test_english_colliding_words_are_excluded(word: str) -> None:
    """Documented exclusions; a future edit that adds one back fails here."""
    assert word not in MARKERS


# --------------------------------------------------------------------------- #
# The instructions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("register", list(Register))
def test_every_register_has_an_instruction(register: Register) -> None:
    assert register_instruction(register).strip()


def test_the_hinglish_instruction_forbids_the_failure_mode_it_exists_for() -> None:
    """"Reply in Hinglish" alone yields transliterated Sanskrit; say so."""
    instruction = register_instruction(Register.HINGLISH).lower()
    assert "roman" in instruction
    assert "sanskrit" in instruction, "the instruction must rule out transliteration"
    assert "devanagari" in instruction


def test_verse_ids_stay_machine_readable_in_every_register() -> None:
    """Citation enforcement matches BG-2.47; a translated id would break it."""
    for register in Register:
        assert "ALLOWED CITATION IDS" in register_instruction(register)


@pytest.mark.parametrize("register", list(Register))
def test_no_instruction_contains_a_concrete_verse_id(register: Register) -> None:
    """A regression, caught by tests/test_generation.py on 9 Sep 2026.

    These instructions go into every turn's prompt. An example id written out
    in full - "keep ids as given (BG-2.47)" - is an id the model can see and
    therefore cite, without ever having been handed that verse. That costs a
    corrective retry and, if the retry also strays, degrades the turn to the
    template answer. So the instructions name where the ids come from instead
    of showing one.
    """
    from corpus.schemas import extract_verse_ids

    assert extract_verse_ids(register_instruction(register)) == []
