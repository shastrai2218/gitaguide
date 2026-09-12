"""Offline LLM provider - the thing that makes rule 1 possible.

Research-pipeline role
---------------------
``FakeLLMProvider`` is not a stub: it produces *structurally valid* output for
each of the three LLM tasks in the pipeline, so the full chain (emotion ->
intent -> safety -> retrieval -> generation -> memory) runs end to end with no
API key, and the citation-enforcement and grounding checks in
:mod:`app.pipeline.generation` are exercised for real.

It routes on the ``TASK:`` marker that every prompt builder writes as its first
line.  Deterministic by construction, which is what the unit tests rely on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from app.llm.base import (
    TASK_ANSWER,
    TASK_INTENT,
    TASK_ROUTE,
    TASK_SUMMARY,
    LLMProvider,
    LLMResponse,
)

TASK_MARKER_RE = re.compile(r"^TASK:\s*([a-z_]+)", re.IGNORECASE | re.MULTILINE)
VERSE_BLOCK_RE = re.compile(
    r"^\[(?P<id>(?:BG|AS)-[\d.]+)\]\s*(?P<citation>.*?)$"
    r"(?P<body>(?:\n(?!\[).*)*)",
    re.MULTILINE,
)
TEXT_LINE_RE = re.compile(r"^Text:\s*(.+)$", re.MULTILINE)
WHY_LINE_RE = re.compile(r"^Why retrieved:\s*(.+)$", re.MULTILINE)
USER_MESSAGE_RE = re.compile(r"^USER MESSAGE:\s*(.+)$", re.MULTILINE)
#: The routing prompt labels the message ``MESSAGE:`` (not ``USER MESSAGE:``),
#: so the two cannot pick up each other's line.
ROUTE_MESSAGE_RE = re.compile(r"^MESSAGE:\s*(.+)$", re.MULTILINE)
EMOTION_LINE_RE = re.compile(r"^DETECTED EMOTION:\s*(.+)$", re.MULTILINE)


class FakeLLMProvider(LLMProvider):
    """Deterministic in-process provider used in mock mode and in tests."""

    name = "fake"
    model = "fake-deterministic-v1"

    def __init__(self, scripted: Sequence[str] | None = None) -> None:
        #: If provided, responses are popped from this queue in order, which
        #: lets a test force malformed or unsafe output.
        self.scripted: list[str] = list(scripted or [])
        self.calls: list[str] = []

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> LLMResponse:
        self.calls.append(prompt)
        if self.scripted:
            return self._respond(self.scripted.pop(0), prompt)

        match = TASK_MARKER_RE.search(prompt)
        task = (match.group(1).lower() if match else "").strip()
        if task == TASK_INTENT:
            body = self._intent(prompt)
        elif task == TASK_ANSWER:
            body = self._answer(prompt)
        elif task == TASK_SUMMARY:
            body = self._summary(prompt)
        elif task == TASK_ROUTE:
            body = self._route(prompt)
        else:
            body = "I do not have enough grounded material to answer that yet."
        return self._respond(body, prompt)

    def _respond(self, body: str, prompt: str) -> LLMResponse:
        return LLMResponse(
            text=body,
            provider=self.name,
            model=self.model,
            prompt_tokens=max(1, len(prompt) // 4),
            completion_tokens=max(1, len(body) // 4),
            latency_ms=0.0,
        )

    @property
    def is_fake(self) -> bool:
        return True

    # ------------------------------------------------------------- tasks
    @staticmethod
    def _intent(prompt: str) -> str:
        """Zero-shot intent, approximated by the shared rule prior."""
        from app.pipeline.intent import heuristic_intent_scores  # local: avoids cycle

        message = ""
        match = USER_MESSAGE_RE.search(prompt)
        if match:
            message = match.group(1)
        else:
            message = prompt.splitlines()[-1] if prompt.strip() else ""

        scores = heuristic_intent_scores(message)
        best = max(scores.items(), key=lambda kv: kv[1])
        return json.dumps(
            {
                "intent": best[0],
                "confidence": round(best[1], 3),
                "scores": {k: round(v, 3) for k, v in scores.items()},
                "rationale": "keyword prior over the six-class taxonomy (offline provider)",
            }
        )

    @staticmethod
    def _route(prompt: str) -> str:
        """Offline conversational routing: a transparent keyword heuristic.

        Deliberately weak, and deliberately biased toward ``guidance``. The
        stage-1 lexicon has already handled greetings and acknowledgements
        before any provider is called, so what reaches here is meta, follow-up,
        guidance or out-of-scope - and of those, guidance is the only one whose
        mis-routing costs a user a real answer. So only an unambiguous meta or
        follow-up marker moves it off the default.

        This is not a model and must not be reported as router accuracy: the
        Phase 1b measurement runs against the real cheap tier.
        """
        match = ROUTE_MESSAGE_RE.search(prompt)
        message = (match.group(1) if match else "").strip().lower()
        has_history = "PREVIOUS EXCHANGE: none" not in prompt

        meta_markers = (
            "who are you", "what are you", "is this ai", "are you ai", "are you a bot",
            "what can you do", "how do you work", "edition", "translation do you",
            "can i trust", "who made you", "what is arthashastra", "what is the gita",
            "tum kya kar sakte ho", "tum kaun ho", "aap kaun ho",
        )
        followup_markers = (
            "aur kya", "explain that", "that verse", "what if he", "what if she",
            "what if they", "give an example", "an example", "simpler", "in hindi",
            "say more", "tell me more", "aur batao",
        )
        out_of_scope_markers = (
            "recipe", "write code", "python", "javascript", "my homework",
            "translate this", "weather", "cricket score", "stock price",
        )

        route = "guidance"
        if any(marker in message for marker in meta_markers):
            route = "meta"
        elif any(marker in message for marker in out_of_scope_markers):
            route = "out_of_scope"
        elif has_history and any(marker in message for marker in followup_markers):
            route = "followup"

        standalone = message if route in {"guidance", "followup"} else ""
        return json.dumps({"route": route, "standalone_query": standalone})

    @staticmethod
    def _answer(prompt: str) -> str:
        """Compose a grounded answer from the CONTEXT block in the prompt."""
        verses = _parse_context_blocks(prompt)
        if not verses:
            return (
                "I could not find anything in the Gita or the Arthashastra that speaks to "
                "this closely enough to quote, so I would rather not improvise."
            )

        emotion = "what you are feeling"
        match = EMOTION_LINE_RE.search(prompt)
        if match:
            emotion = match.group(1).split("(")[0].strip() or emotion

        primary = verses[0]
        others = verses[1:3]

        lines = [
            f"What you describe reads as {emotion}, and the texts do speak to it directly.",
            "",
            f"**{primary['citation']}** puts it plainly: {primary['text']} "
            f"Read against your situation, that means the part you actually control is the "
            f"effort and the conduct, not the verdict that comes back.",
        ]
        for verse in others:
            lines.append("")
            lines.append(
                f"**{verse['citation']}** adds the other half: {verse['text']} "
                f"Practically, that is the instruction to act on what is in front of you "
                f"today rather than on the whole outcome at once."
            )

        lines += ["", "### Why this verse", ""]
        for verse in verses[:3]:
            lines.append(f"- **{verse['id']}** - {verse['why']}")
        lines += [
            "",
            "If you tell me what the next concrete decision is, I can narrow this to that "
            "one step.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _summary(prompt: str) -> str:
        """Extractive rolling summary: keep the user's own phrasing."""
        turns = re.findall(r"^(?:user|assistant):\s*(.+)$", prompt, re.MULTILINE | re.IGNORECASE)
        if not turns:
            return "No prior conversation."
        kept = [turn.strip() for turn in turns if turn.strip()][:6]
        joined = " ".join(kept)
        if len(joined) > 700:
            joined = joined[:697] + "..."
        return f"Earlier in this conversation: {joined}"


def _parse_context_blocks(prompt: str) -> list[dict[str, str]]:
    """Extract ``[VERSE-ID] ... Text: ... Why retrieved: ...`` blocks."""
    blocks: list[dict[str, str]] = []
    for match in VERSE_BLOCK_RE.finditer(prompt):
        body = match.group("body") or ""
        text_match = TEXT_LINE_RE.search(body)
        why_match = WHY_LINE_RE.search(body)
        blocks.append(
            {
                "id": match.group("id"),
                "citation": (match.group("citation") or match.group("id")).strip(),
                "text": (text_match.group(1).strip() if text_match else "").strip(),
                "why": (why_match.group(1).strip() if why_match else "vector similarity match"),
            }
        )
    return blocks
