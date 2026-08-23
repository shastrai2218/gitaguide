"""Stage 5 - grounded generation with enforced citations.

Research-pipeline role
---------------------
The model is allowed to reason, but not to source. Everything it asserts about
the texts must come from the verses handed to it in the CONTEXT block, and every
verse it uses must be cited by id (``BG-2.47``, ``AS-1.2.3``). The answer must
also end with a **"Why this verse"** section that restates the graph path that
caused each verse to be retrieved - that section is the user-facing form of the
system's explainability claim.

Enforcement is mechanical, not hopeful:

1. citations are extracted from the completion and diffed against the retrieved
   set (``hallucinated_citations``);
2. if anything is hallucinated, or nothing valid was cited, the generator
   **retries once** with a corrective instruction;
3. if it still fails, the offending ids are stripped and a deterministic answer
   is composed from the retrieved verses, so an ungrounded answer can never
   reach the user;
4. a missing "Why this verse" section is appended from the graph paths.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Collection, Sequence

from app.config import Settings, get_settings
from app.llm.base import TASK_ANSWER, LLMProvider, LLMResponse
from app.pipeline.memory import MemoryContext
from app.pipeline.schemas import (
    EmotionResult,
    GenerationResult,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
    RetrievedVerse,
)
from corpus.schemas import VERSE_ID_RE, extract_verse_ids

logger = logging.getLogger(__name__)

WHY_HEADING = "### Why this verse"

SYSTEM_PROMPT = """You are GitaGuide, a guidance companion grounded in the Bhagavad Gita and \
Kautilya's Arthashastra.

Absolute rules:
1. Use ONLY the verses in the CONTEXT block. If they do not cover the question, say so plainly \
instead of drawing on anything else you know.
2. Cite every verse you use by its id in the form BG-2.47 or AS-1.2.3. Never invent an id, a \
chapter number or a translation.
3. Do not present yourself as a therapist, doctor, lawyer or priest, and never tell someone to \
stop medical treatment.
4. Speak to the person's actual situation in plain, warm, concrete language. No sermon, no \
Sanskrit padding, no promises about outcomes.
5. End with a section titled exactly "### Why this verse" that lists each cited verse and the \
retrieval path that surfaced it, restated in one clause a lay reader can follow.

Length: 180-320 words before the "Why this verse" section."""

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
    """Assemble the grounded-generation prompt."""
    intent_label = intent.intent if intent else IntentLabel.SEEK_GUIDANCE
    emotion_line = "DETECTED EMOTION: not detected"
    if emotion is not None and emotion.labels:
        tops = ", ".join(f"{item.emotion} ({item.score:.2f})" for item in emotion.labels[:3])
        emotion_line = f"DETECTED EMOTION: {tops} | intensity {emotion.intensity:.2f}"

    memory_block = memory.render() if memory and not memory.is_empty else ""
    allowed = ", ".join(retrieval.verse_ids) or "none"

    parts = [
        f"TASK: {TASK_ANSWER}",
        emotion_line,
        f"DETECTED INTENT: {intent_label.value} - {INTENT_FRAMING.get(intent_label, '')}",
    ]
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
        response = await self._complete(prompt)
        answer, citations, hallucinated = self._validate(response.text, allowed)

        if hallucinated or not citations:
            note = self._corrective_note(hallucinated, allowed)
            logger.warning("regenerating: %s", note)
            retry_prompt = build_answer_prompt(
                query, retrieval, emotion, intent, memory, corrective_note=note
            )
            retry = await self._complete(retry_prompt)
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
        answer = self._ensure_why_section(answer, why_map)

        return GenerationResult(
            answer=answer.strip(),
            citations=citations,
            why_this_verse=why_map,
            grounded=bool(citations) and not hallucinated,
            hallucinated_citations=hallucinated,
            provider=response.provider,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    # ---------------------------------------------------------------- pieces
    async def _complete(self, prompt: str) -> LLMResponse:
        try:
            return await self.llm.complete(
                prompt,
                system=SYSTEM_PROMPT,
                max_tokens=self.settings.llm_max_tokens,
                temperature=self.settings.llm_temperature,
            )
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
    def _ensure_why_section(answer: str, why_map: dict[str, str]) -> str:
        """Append the explanation section when the model forgot it."""
        if not why_map:
            return answer
        if "why this verse" in answer.lower():
            return answer
        lines = [answer.rstrip(), "", WHY_HEADING, ""]
        lines += [f"- **{verse_id}** - {why}" for verse_id, why in why_map.items()]
        return "\n".join(lines)

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
        """Deterministic grounded answer, used only if the LLM cannot comply."""
        feeling = emotion.dominant if emotion and emotion.labels else "what you are carrying"
        lines = [
            f"Reading your message, the strongest thread is {feeling}. "
            "Here is what the texts actually say about it, quoted rather than paraphrased.",
            "",
        ]
        for item in retrieval.results[:3]:
            lines.append(f"**{item.verse.citation}** - {item.verse.text_english.strip()}")
            lines.append("")
        lines.append(
            "Taken together: the part you control is your own conduct and effort in the next "
            "step, not the outcome that follows it. Choose the one action you can take today, "
            "and treat the rest as not yet yours to settle."
        )
        lines += ["", WHY_HEADING, ""]
        citable = {item.verse_id for item in retrieval.results}
        for item in retrieval.results[:3]:
            why = mask_foreign_verse_ids(item.why(), citable)
            lines.append(f"- **{item.verse_id}** - {why}")
        return "\n".join(lines)
