"""Stage 5 - grounded generation with enforced citations.

Research-pipeline role
---------------------
The model is allowed to reason, but not to source. Everything it asserts about
the texts must come from the verses handed to it in the CONTEXT block, and every
verse it uses must be cited by id (``BG-2.47``, ``AS-1.2.3``).

Enforcement is mechanical, not hopeful:

1. citations are extracted from the completion and diffed against the retrieved
   set (``hallucinated_citations``);
2. if anything is hallucinated, or nothing valid was cited, the generator
   **retries once** with a corrective instruction;
3. if it still fails, the offending ids are stripped and a deterministic answer
   is composed from the retrieved verses, so an ungrounded answer can never
   reach the user;
4. the graph path that surfaced each cited verse is returned in
   ``why_this_verse`` - the explainability claim, delivered beside the citation
   rather than appended to the reply (see :func:`strip_why_section`).

The response contract (tester feedback, 9 Sep 2026)
--------------------------------------------------
Testers asked for shorter, more conversational answers that reference verses
instead of pasting them, in their own language, drawing on both texts at once.
That is enforced here in four places: the system prompt's shape and 80-150 word
limit, :data:`CROSS_SOURCE_INSTRUCTION` added only when the retrieved set truly
spans both scriptures, the register mirrored from the user's own message, and
:func:`asks_for_detail` as the only release from the limit.

Grounding is not coherence
--------------------------
On 8 Sep 2026 the fallback model served a tester this, verbatim::

    I'll just try a different.
    Actually, let you can be the.
    I see the the the
    I the I the
    ### Why this verse
    - **AS-7.9.7**: This verse was retrieved because ...

Every check above passed: the "Why this verse" section was well formed and cited
three retrieved ids, so the turn was ``grounded=True`` with no hallucinations,
and the output safety gate found nothing harmful in it. Nothing in this module
looked at whether the *body* was language. :func:`assess_answer` now does -
word count, repetition, and stutter (one word or one word pair repeated back to
back) over the body before the explanation section - and
:meth:`GroundedGenerator._complete_guarded` retries once on a degenerate
answer, then serves the overload reply rather than word salad.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app import telemetry
from app.config import Settings, get_settings
from app.llm.base import TASK_ANSWER, BudgetExhaustedError, LLMError, LLMProvider, LLMResponse
from app.pipeline.memory import MemoryContext
from app.pipeline.register import detect_register, register_instruction
from app.pipeline.schemas import (
    EmotionResult,
    GenerationResult,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
    RetrievedVerse,
)
from corpus.schemas import VERSE_ID_RE, ScriptureSource, extract_verse_ids

logger = logging.getLogger(__name__)

#: Recorded as the provider of an overload turn, so these are countable in the
#: database and in telemetry instead of being mistaken for a short real answer.
PROVIDER_OVERLOADED = "overloaded"

#: What a tester sees when the whole chain ran out of wall clock. No verses: it
#: would be dishonest to attach scripture to a turn the model never answered.
OVERLOADED_ANSWER = (
    "The guide is overloaded — please try again in a minute.\n\n"
    "Nothing you wrote was lost, and this is not about what you asked. "
    "The models behind GitaGuide are shared and sometimes queue up."
)

WHY_HEADING = "### Why this verse"


# --------------------------------------------------------------------------- #
# Output sanity guard
# --------------------------------------------------------------------------- #
#: Letters in any script. Digits, punctuation and verse ids are not words.
WORD_RE = re.compile(r"[^\W\d_]+")
#: The explanation heading, in whatever form a model writes it. Used to find the
#: body for :func:`assess_answer` and to strip the section in
#: :func:`strip_why_section`.
WHY_HEADING_RE = re.compile(r"^#{1,6}\s*why this verse\b", re.IGNORECASE | re.MULTILINE)
#: One word this many times in a row ("the the the") is not prose.
STUTTER_WORD_RUN = 3
#: One word pair this many times back to back ("I the I the") is not prose.
STUTTER_BIGRAM_RUN = 2


class DegenerateOutputError(LLMError):
    """Two completions in a row read as word salad; nothing fit to serve."""


@dataclass(frozen=True)
class AnswerQuality:
    """What :func:`assess_answer` measured, and why it objected if it did."""

    words: int
    distinct: int
    repetition: float
    longest_word_run: int
    longest_bigram_run: int
    reasons: tuple[str, ...]

    @property
    def degenerate(self) -> bool:
        return bool(self.reasons)

    def summary(self) -> str:
        measured = (
            f"words={self.words} distinct={self.distinct} repetition={self.repetition:.2f} "
            f"word_run={self.longest_word_run} bigram_run={self.longest_bigram_run}"
        )
        return f"{measured}; {'; '.join(self.reasons)}" if self.reasons else measured


def answer_body(text: str) -> str:
    """The answer before its "Why this verse" section.

    The explanation section is built from graph paths and verse ids, so it is
    coherent whether or not the answer above it is - the production salad had
    a perfectly formed one. Judging the whole text would let the section
    launder the body: 21 words of noise plus 170 of explanation scored as 193
    words with 112 distinct. Only the body is a claim about language.
    """
    match = WHY_HEADING_RE.search(text)
    if match is None:
        return text
    body = text[: match.start()]
    return body if body.strip() else text


def strip_why_section(text: str) -> str:
    """Remove a "Why this verse" section from the answer body.

    Why the explanation moved out of the chat
    -----------------------------------------
    The section used to be required by the system prompt and appended by the
    generator when the model forgot it. It was the user-facing form of the
    project's explainability claim, and it is being **kept** - but not in the
    reply, because it cannot coexist with an 80-150 word answer. Testers asked
    for shorter answers (item 1) and for references rather than pasted verses
    (item 4); the section was 50-170 words of restated graph paths on top of
    every reply.

    So the explanation is now delivered structurally instead of in prose:
    ``GenerationResult.why_this_verse`` still carries verse id -> graph path for
    every citation, ``GET /explain/{id}`` still returns the whole trace, and the
    beta UI shows the path next to each verse. Nothing about the claim is lost;
    what changes is that the reader gets it beside the citation instead of
    inside the answer.

    The prompt now asks the model not to write the section, and this strips it
    if the model writes one anyway - which it does out of habit, and which no
    prompt can guarantee against.

    >>> strip_why_section("Do the work.\\n\\n### Why this verse\\n\\n- BG-2.47 - x")
    'Do the work.'
    >>> strip_why_section("Just the answer.")
    'Just the answer.'
    """
    match = WHY_HEADING_RE.search(text)
    if match is None:
        return text.strip()
    body = text[: match.start()].strip()
    # A model that wrote *only* the section left no answer to keep; returning
    # the original lets the quality guard judge it rather than silently
    # shipping an empty reply.
    return body or text.strip()


def _longest_run(items: Sequence[object]) -> int:
    longest = current = 0
    previous: object = None
    for item in items:
        current = current + 1 if item == previous else 1
        previous = item
        longest = max(longest, current)
    return longest


def _longest_bigram_run(words: Sequence[str]) -> int:
    """How many times one word pair repeats back to back, at either alignment."""
    longest = 0
    for offset in (0, 1):
        pairs = [tuple(words[index : index + 2]) for index in range(offset, len(words) - 1, 2)]
        longest = max(longest, _longest_run(pairs))
    return longest


def assess_answer(text: str, settings: Settings | None = None) -> AnswerQuality:
    """Decide whether a completion reads as an answer at all.

    Three cheap measurements over the body's words, each of which the 8 Sep
    reply fails or nearly fails and no legitimate reply observed comes near:

    * **length** - below ``output_guard_min_words`` the text is a fragment, not
      an answer to a prompt that asked for 180-320 words;
    * **repetition** - ``1 - distinct/total`` above ``output_guard_max_repetition``
      is a loop ("I the I the I the ..."); prose sits near 0.4-0.5;
    * **stutter** - one word ``STUTTER_WORD_RUN`` times in a row, or one word
      pair ``STUTTER_BIGRAM_RUN`` times back to back. This is the check that
      catches the actual production text ("the the the", "I the I the"), whose
      21 words and 0.38 repetition would have passed the other two.

    It is a language check, not a quality check: a coherent wrong answer passes,
    and should - the citation enforcer and the output gate judge those.
    """
    settings = settings or get_settings()
    words = [word.lower() for word in WORD_RE.findall(answer_body(text))]
    total = len(words)
    distinct = len(set(words))
    repetition = 1.0 - distinct / total if total else 1.0
    word_run = _longest_run(words)
    bigram_run = _longest_bigram_run(words)

    reasons: list[str] = []
    floor = settings.output_guard_min_words
    if total < floor:
        reasons.append(f"{total} words in the body, below the floor of {floor}")
    elif repetition > settings.output_guard_max_repetition:
        reasons.append(
            f"repetition {repetition:.2f} above {settings.output_guard_max_repetition:.2f}"
        )
    if word_run >= STUTTER_WORD_RUN:
        reasons.append(f"one word repeated {word_run} times in a row")
    if bigram_run >= STUTTER_BIGRAM_RUN:
        reasons.append(f"one word pair repeated {bigram_run} times back to back")

    return AnswerQuality(
        words=total,
        distinct=distinct,
        repetition=round(repetition, 4),
        longest_word_run=word_run,
        longest_bigram_run=bigram_run,
        reasons=tuple(reasons),
    )


SYSTEM_PROMPT = """You are GitaGuide, a guidance companion grounded in the Bhagavad Gita and \
Kautilya's Arthashastra.

You are talking with a person, not answering a query. Speak to their situation first, in their \
own terms, and only then bring the teaching to it.

Absolute rules:
1. Use ONLY the verses in the CONTEXT block. If they do not cover the question, say so plainly \
instead of drawing on anything else you know.
2. Cite by id, in the form BG-2.47 or AS-1.2.3, and cite AT MOST TWO. Never invent an id, a \
chapter number or a translation.
3. Do NOT quote, paste or transliterate the verse text itself, and do not include Sanskrit. The \
reader is shown each verse's reference and full text separately, so repeating it here makes the \
answer twice as long and no more useful. Say what the verse means in your own words.
4. Do not present yourself as a therapist, doctor, lawyer or priest, and never tell someone to \
stop medical treatment.
5. Never narrate your own process - no "The user is asking...", no "The context provides...", no \
"As an AI". Do not write a heading, a section title, or a bulleted list of your reasons.

Shape of the answer:
- Open with the single thing you would most want them to take away, in one sentence.
- Then two or three sentences connecting it to what they actually described, naming at most two \
verses by id.
- Close with one short question that would move things forward, when a question is natural. Not \
every turn needs one.

Length: 80-150 words in total. Go longer ONLY if the person explicitly asks for detail, for the \
full verse, or for an explanation at length."""

#: Added when the retrieved set spans both scriptures. Tester feedback item 3:
#: "combine answer of both gita and arthasastra". Listing them one after the
#: other is what the model does unprompted, and it reads like two answers.
CROSS_SOURCE_INSTRUCTION = (
    "BOTH TEXTS APPLY: the context holds verses from the Gita and from the Arthashastra. "
    "Weave them into one thought rather than listing them - the Gita for the inner "
    "disposition (what to hold onto, what to let go of), the Arthashastra for the practical "
    "conduct (what to actually do, and in what order). Join them in a sentence; do not give "
    "them a paragraph each."
)

#: Phrases that license a longer answer. Deliberately narrow: bare "explain" is
#: how people ask ordinary questions ("can you explain what to do?"), so it is
#: not here. Only an explicit request for *more* counts.
DETAIL_REQUESTS: tuple[str, ...] = (
    "in detail", "more detail", "in depth", "at length", "elaborate", "expand on",
    "tell me more", "say more", "more about", "full verse", "full text", "whole verse",
    "entire verse", "long answer", "detailed answer", "vistaar", "detail me",
    "poora shlok", "pura shlok", "poori", "vistar se",
)


def asks_for_detail(query: str) -> bool:
    """True when the person explicitly asked for a longer answer.

    >>> asks_for_detail("what should I do?")
    False
    >>> asks_for_detail("Can you explain that in more detail?")
    True
    """
    lowered = query.lower()
    return any(phrase in lowered for phrase in DETAIL_REQUESTS)

INTENT_FRAMING: dict[IntentLabel, str] = {
    IntentLabel.SEEK_GUIDANCE: (
        "They want direction. Name the choice in front of them, then give one concrete next step."
    ),
    IntentLabel.EMOTIONAL_SUPPORT: (
        "They mainly need to be heard. Acknowledge the feeling first, in their own terms, and "
        "keep the scripture light - one or two verses, no lecture."
    ),
    IntentLabel.PROFESSIONAL_DILEMMA: (
        "This is a work decision. Be practical: the Arthashastra is a manual of statecraft, so "
        "translate it into workplace terms (audit, delegation, timing, negotiation)."
    ),
    IntentLabel.SCRIPTURE_QUESTION: (
        "This is an exegetical question. Explain what the verse says, what it is responding to, "
        "and how it is read - do not turn it into personal advice unless they asked."
    ),
    IntentLabel.CRISIS: (
        "Safety takes precedence over teaching. Keep it short, warm and non-directive."
    ),
    IntentLabel.OTHER: "Answer briefly and offer what you can actually help with.",
}


#: Stands in for a verse id that the model is not allowed to cite.
FOREIGN_ID_PLACEHOLDER = "a related verse not included here"


def mask_foreign_verse_ids(
    text: str,
    allowed: Collection[str],
    placeholder: str = FOREIGN_ID_PLACEHOLDER,
) -> str:
    """Replace verse ids that are **not** in ``allowed`` with a placeholder.

    Why this exists
    ---------------
    Graph paths are 1-2 hop, so a ``Why retrieved:`` line names the *seed* verse
    of the hop - and the seed is frequently not itself in the top-k::

        Why retrieved: (BG-3.35) -[:HAS_THEME]-> theme 'duty' <-[:HAS_THEME]- (BG-18.47)

    A model reading that cites ``BG-3.35``, whose text it was never given. The
    citation enforcer in :class:`GroundedGenerator` catches it, but only by
    spending a second LLM call on a rewrite - and if the rewrite also strays, the
    answer degrades to the deterministic template. On a rate-limited endpoint
    that is the difference between one request per turn and two.

    Masking happens **only** where text is shown to the model or placed in the
    answer body. :attr:`RetrievedVerse.graph_paths` and
    :meth:`RetrievedVerse.why` are untouched, so the explanation panel and the
    stored trace still show the real, complete path.

    >>> mask_foreign_verse_ids("(BG-3.35) -> (BG-2.47)", {"BG-2.47"})
    '(a related verse not included here) -> (BG-2.47)'
    """
    permitted = set(allowed)

    def replace(match: re.Match[str]) -> str:
        verse_id = match.group(0)
        return verse_id if verse_id in permitted else placeholder

    return VERSE_ID_RE.sub(replace, text)


#: Both scriptures present in one retrieved set - the case tester item 3 asks
#: to be woven rather than listed.
BOTH_SOURCES: frozenset[ScriptureSource] = frozenset(
    {ScriptureSource.BHAGAVAD_GITA, ScriptureSource.ARTHASHASTRA}
)


def retrieved_sources(retrieval: RetrievalTrace) -> frozenset[ScriptureSource]:
    """Which scriptures the retrieved set actually spans.

    Read from the retrieval trace, not from the query or the intent: the
    cross-source instruction must only fire when both texts are genuinely
    available to cite, or it invites the model to reach for a verse it was
    never given.

    >>> retrieved_sources(RetrievalTrace(query="q", results=[])) == frozenset()
    True
    """
    return frozenset(item.verse.source for item in retrieval.results)


def format_context_block(results: Sequence[RetrievedVerse]) -> str:
    """Render retrieved verses in the exact shape the parsers expect.

    The layout (``[ID] citation`` / ``Text:`` / ``Why retrieved:``) is also what
    :class:`app.llm.fake.FakeLLMProvider` reads, which keeps offline runs
    structurally identical to online ones.

    Every verse id the model can see here is one it may cite: ids reachable only
    as graph-walk seeds are masked by :func:`mask_foreign_verse_ids`.
    """
    citable = {item.verse_id for item in results}
    blocks: list[str] = []
    for item in results:
        verse = item.verse
        lines = [f"[{verse.id}] {verse.citation}", f"Text: {verse.text_english.strip()}"]
        if verse.text_sanskrit:
            lines.append(f"Sanskrit: {verse.text_sanskrit.strip()}")
        if verse.themes:
            lines.append(f"Themes: {', '.join(verse.themes)}")
        lines.append(f"Why retrieved: {mask_foreign_verse_ids(item.why(), citable)}")
        lines.append(
            "Scores: "
            f"vector={item.vector_score:.3f} fulltext={item.fulltext_score:.3f} "
            f"graph={item.graph_score:.3f} final={item.final_score:.3f}"
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_answer_prompt(
    query: str,
    retrieval: RetrievalTrace,
    emotion: EmotionResult | None = None,
    intent: IntentResult | None = None,
    memory: MemoryContext | None = None,
    corrective_note: str = "",
) -> str:
    """Assemble the grounded-generation prompt.

    Three instructions are conditional on the turn rather than fixed, so the
    prompt says only what applies:

    * the **register** to answer in, mirrored from the user's own message
      (:mod:`app.pipeline.register`);
    * the **cross-source** instruction, only when the retrieved set actually
      spans both scriptures - telling the model to weave two texts when it has
      verses from one invites it to invent the other;
    * a **long-form** release, only when the person asked for detail.
    """
    intent_label = intent.intent if intent else IntentLabel.SEEK_GUIDANCE
    emotion_line = "DETECTED EMOTION: not detected"
    if emotion is not None and emotion.labels:
        tops = ", ".join(f"{item.emotion} ({item.score:.2f})" for item in emotion.labels[:3])
        emotion_line = f"DETECTED EMOTION: {tops} | intensity {emotion.intensity:.2f}"

    memory_block = memory.render() if memory and not memory.is_empty else ""
    allowed = ", ".join(retrieval.verse_ids) or "none"
    detected = detect_register(query)

    parts = [
        f"TASK: {TASK_ANSWER}",
        f"REGISTER: {register_instruction(detected.register)}",
        emotion_line,
        f"DETECTED INTENT: {intent_label.value} - {INTENT_FRAMING.get(intent_label, '')}",
    ]
    if retrieved_sources(retrieval) == BOTH_SOURCES:
        parts.append(CROSS_SOURCE_INSTRUCTION)
    if asks_for_detail(query):
        parts.append(
            "LENGTH OVERRIDE: they asked for more, so the 80-150 word limit does not apply "
            "to this turn. Still do not paste the verse text."
        )
    if memory_block:
        parts.append(memory_block)
    parts += [
        "CONTEXT (the only verses you may use):",
        format_context_block(retrieval.results),
        f"ALLOWED CITATION IDS: {allowed}",
        f"USER MESSAGE: {query}",
    ]
    if corrective_note:
        parts.append(f"CORRECTION: {corrective_note}")
    return "\n\n".join(parts)


class GroundedGenerator:
    """Stage-5 generator: prompt, call, verify, repair.

    Examples
    --------
    >>> import asyncio
    >>> from app.llm.fake import FakeLLMProvider
    >>> from app.pipeline.retrieval import InMemoryHybridRetriever
    >>> gen = GroundedGenerator(FakeLLMProvider())
    >>> trace = asyncio.run(InMemoryHybridRetriever().retrieve("I am anxious about work"))
    >>> result = asyncio.run(gen.generate("I am anxious about work", trace))
    >>> result.grounded and bool(result.citations)
    True
    """

    def __init__(self, llm: LLMProvider, settings: Settings | None = None) -> None:
        self.llm = llm
        self.settings = settings or get_settings()

    async def generate(
        self,
        query: str,
        retrieval: RetrievalTrace,
        emotion: EmotionResult | None = None,
        intent: IntentResult | None = None,
        memory: MemoryContext | None = None,
    ) -> GenerationResult:
        """Produce a grounded, cited answer for one turn."""
        started = time.perf_counter()
        allowed = set(retrieval.verse_ids)

        if not allowed:
            return GenerationResult(
                answer=self._no_context_answer(),
                citations=[],
                grounded=False,
                provider=self.llm.name,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        prompt = build_answer_prompt(query, retrieval, emotion, intent, memory)
        guard_notes: list[str] = []
        try:
            response = await self._complete_guarded(prompt, guard_notes)
        except BudgetExhaustedError as exc:
            return self._overloaded_result(started, exc)
        except DegenerateOutputError as exc:
            return self._degenerate_result(started, exc)
        answer, citations, hallucinated = self._validate(response.text, allowed)

        if hallucinated or not citations:
            note = self._corrective_note(hallucinated, allowed)
            logger.warning("regenerating: %s", note)
            retry_prompt = build_answer_prompt(
                query, retrieval, emotion, intent, memory, corrective_note=note
            )
            try:
                retry = await self._complete_guarded(retry_prompt, guard_notes)
            except BudgetExhaustedError as exc:
                return self._overloaded_result(started, exc)
            except DegenerateOutputError as exc:
                return self._degenerate_result(started, exc)
            retry_answer, retry_citations, retry_hallucinated = self._validate(
                retry.text, allowed
            )
            if retry_citations and not retry_hallucinated:
                response, answer, citations, hallucinated = (
                    retry,
                    retry_answer,
                    retry_citations,
                    retry_hallucinated,
                )
            else:
                answer, citations = retry_answer, retry_citations
                hallucinated = retry_hallucinated

        if not citations:
            # Last resort: compose from the retrieved verses so the user never
            # receives an answer that is not traceable to the corpus.
            answer = self._template_answer(query, retrieval, emotion)
            citations = extract_verse_ids(answer)
            hallucinated = []
            logger.warning("LLM failed citation enforcement twice; used template answer")

        why_map = self._why_map(retrieval, citations)
        answer = strip_why_section(answer)

        fallback_steps = int(response.raw.get("fallback_steps", 0) or 0)
        if response.provider.startswith("fake"):
            # Not a graceful degradation: a deterministic template answer has
            # just been handed to a user as guidance. Loud on purpose.
            logger.warning(
                "FAKE PROVIDER ANSWERED a live turn (fallback_steps=%d). The real "
                "providers were unreachable or unconfigured; this answer is a "
                "deterministic template, not a generated one.",
                fallback_steps,
            )

        return GenerationResult(
            answer=answer.strip(),
            citations=citations,
            why_this_verse=why_map,
            grounded=bool(citations) and not hallucinated,
            hallucinated_citations=hallucinated,
            provider=response.provider,
            model=response.model,
            fallback_steps=fallback_steps,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            quality_guard="retried" if guard_notes else "",
            quality_guard_detail=" | ".join(guard_notes),
            answer_register=detect_register(query).register.value,
        )

    # ---------------------------------------------------------------- pieces
    async def _complete_guarded(self, prompt: str, notes: list[str]) -> LLMResponse:
        """One completion, retried once if it reads as word salad.

        The retry goes back through the same chain, so the primary model gets
        the first attempt at it even when the salad came from the fallback -
        which is where the 8 Sep reply came from. If the second answer is also
        degenerate, or the provider returned nothing, nothing fit to serve
        exists and :class:`DegenerateOutputError` says so; the caller turns
        that into the overload reply. Never the salad, never a template dressed
        as an answer.

        An *empty* first completion is left to the existing path (citation
        check, corrective rewrite, template): it is a provider failure, not
        language, and it was handled before this guard existed.
        """
        response = await self._complete(prompt)
        quality = assess_answer(response.text, self.settings)
        if not response.text.strip() or not quality.degenerate:
            return response

        telemetry.count("degenerate_output_detected")
        first = response.model or response.provider
        logger.warning("degenerate answer from %s (%s); retrying once", first, quality.summary())

        retry = await self._complete(prompt)
        retry_quality = assess_answer(retry.text, self.settings)
        if retry.text.strip() and not retry_quality.degenerate:
            telemetry.count("degenerate_output_recovered")
            notes.append(f"first answer from {first} was degenerate ({quality.summary()})")
            return retry

        telemetry.count("degenerate_output_shed")
        second = retry.model or retry.provider
        raise DegenerateOutputError(
            f"first answer from {first}: {quality.summary()}; retry from {second}: "
            + (retry_quality.summary() if retry.text.strip() else "empty completion")
        )

    def _degenerate_result(self, started: float, exc: DegenerateOutputError) -> GenerationResult:
        """The overload reply, for a turn where both completions were word salad.

        The same shape and text as a budget-exhausted turn - the tester is told
        the guide could not answer, not shown the noise - but marked
        ``quality_guard="shed"`` with the models and measurements in the detail,
        so the two causes stay separable in the database and in telemetry.
        """
        logger.error("degenerate output twice; serving the overload reply (%s)", exc)
        return GenerationResult(
            answer=OVERLOADED_ANSWER,
            citations=[],
            why_this_verse={},
            grounded=False,
            provider=PROVIDER_OVERLOADED,
            model="",
            quality_guard="shed",
            quality_guard_detail=str(exc),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def _overloaded_result(
        self, started: float, exc: BudgetExhaustedError
    ) -> GenerationResult:
        """A normal-shaped turn saying the guide is overloaded.

        Not a 500, and not a template answer dressed up as guidance. The tester
        asked something real and deserves to know the system could not get to it
        in time, in the same shape as every other reply, so the UI renders it
        without a special case.

        ``provider="overloaded"`` is persisted with the turn, so these are
        countable afterwards rather than looking like ordinary short answers.
        """
        logger.error("generation budget exhausted; serving the overload reply (%s)", exc)
        return GenerationResult(
            answer=OVERLOADED_ANSWER,
            citations=[],
            why_this_verse={},
            grounded=False,
            provider=PROVIDER_OVERLOADED,
            model="",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )


    async def _complete(self, prompt: str) -> LLMResponse:
        try:
            return await self.llm.complete(
                prompt,
                system=SYSTEM_PROMPT,
                max_tokens=self.settings.llm_max_tokens,
                temperature=self.settings.llm_temperature,
            )
        except BudgetExhaustedError:
            # Not swallowed: the caller has to be able to tell "slow" from
            # "broken", because the honest reply differs.
            raise
        except Exception:
            logger.exception("LLM completion failed")
            return LLMResponse(text="", provider=self.llm.name, model=self.llm.model)

    @staticmethod
    def _validate(text: str, allowed: set[str]) -> tuple[str, list[str], list[str]]:
        """Split cited ids into valid/hallucinated and strip the bad ones."""
        cited = extract_verse_ids(text)
        valid = [verse_id for verse_id in cited if verse_id in allowed]
        hallucinated = [verse_id for verse_id in cited if verse_id not in allowed]
        cleaned = text
        for verse_id in hallucinated:
            cleaned = re.sub(rf"\[?{re.escape(verse_id)}\]?", "[citation removed]", cleaned)
        return cleaned, valid, hallucinated

    @staticmethod
    def _corrective_note(hallucinated: Sequence[str], allowed: set[str]) -> str:
        allowed_list = ", ".join(sorted(allowed))
        if hallucinated:
            return (
                f"Your previous answer cited {', '.join(hallucinated)}, which was NOT in the "
                f"context. Rewrite it using only these ids: {allowed_list}."
            )
        return (
            "Your previous answer cited no verse. Rewrite it, citing at least one of these "
            f"ids inline: {allowed_list}."
        )

    @staticmethod
    def _why_map(retrieval: RetrievalTrace, citations: Sequence[str]) -> dict[str, str]:
        """``verse_id -> graph-path explanation`` for the cited verses.

        Paths are masked the same way as the prompt: this text is appended to the
        **answer**, and naming a verse the reader was never shown reads like a
        citation they cannot check. The unmasked path stays in
        ``retrieval.results[].graph_paths`` for the explanation panel.
        """
        by_id = {item.verse_id: item for item in retrieval.results}
        citable = set(by_id)
        out: dict[str, str] = {}
        for verse_id in citations:
            item = by_id.get(verse_id)
            if item is None:
                continue
            channels = "+".join(item.retrieval_channels) or "vector"
            why = mask_foreign_verse_ids(item.why(), citable)
            out[verse_id] = f"{why} [channels: {channels}, score {item.final_score:.3f}]"
        return out

    @staticmethod
    def _no_context_answer() -> str:
        return (
            "I could not find anything in the Bhagavad Gita or the Arthashastra that speaks "
            "closely enough to this to quote honestly, and I would rather not improvise "
            "scripture at you.\n\n"
            "If you can tell me a bit more - what the situation is, what you are weighing, "
            "and what you are feeling most strongly - I will look again."
        )

    @staticmethod
    def _template_answer(
        query: str,
        retrieval: RetrievalTrace,
        emotion: EmotionResult | None,
    ) -> str:
        """Deterministic grounded answer, used only if the LLM cannot comply.

        It used to paste each verse's full text, which is exactly what tester
        item 4 asked us to stop doing ("Dont show direct slog directly just
        give it reference link"). It now names the references and leaves the
        text to the citation surface, like every other answer. Grounding is
        unaffected: the ids are still in the body, so ``extract_verse_ids``
        still finds them and ``grounded`` still holds.
        """
        feeling = emotion.dominant if emotion and emotion.labels else "what you are carrying"
        references = [item.verse_id for item in retrieval.results[:2]]
        named = " and ".join(references)
        return (
            f"The part you can act on is your own conduct and effort in the next step, not "
            f"the outcome that follows it.\n\n"
            f"Reading your message, the strongest thread is {feeling}. "
            f"{named} speak to exactly this, and they are shown below with their full text "
            f"so you can read them yourself rather than take my word for it.\n\n"
            f"What is the one action here that is genuinely yours to take?"
        )
