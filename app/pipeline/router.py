"""Stage 4b - the conversational router: does this message want retrieval at all?

Why this stage exists
---------------------
Closed-beta testers sent "hi", "thanks", "who are you" and "ok", and every one
of them came back with a verse. The pipeline had no way to tell a greeting from
a request for guidance, because nothing ever asked: emotion, intent, retrieval
and generation ran identically on all four words of "kya haal hai" and on a
paragraph about a dying parent. The result reads like a lookup tool with one
response, and it is the single most visible thing separating GitaGuide from
something a person would talk to.

The router answers one question - *what kind of turn is this* - and the answer
selects a reply tier (:mod:`app.pipeline.tiers`), which decides whether
retrieval runs at all and how long the reply should be.

This is NOT the intent classifier, and it is not a rename of it
-----------------------------------------------------------------
:class:`app.pipeline.intent.IntentClassifier` and this module classify
different things, and both are kept:

* ``IntentLabel`` (six classes) says **what the person is asking about** -
  emotional, professional, exegetical - and is consumed by retrieval re-ranking
  as the scripture prior (Gita vs Arthashastra) and by the generation prompt's
  framing. It is a property of the *subject matter*.
* ``Route`` (seven classes) says **what kind of conversational move this is** -
  opening, acknowledging, following up, asking for guidance. It is a property
  of the *turn*, and it is what decides whether retrieval happens.

They are orthogonal: "mera boss pressure deta hai" is ``guidance`` (route) and
``professional_dilemma`` (intent); "aur kya" is ``followup`` (route) and,
inheriting its subject from the previous turn, still ``professional_dilemma``.
Collapsing them into one enum would either lose the scripture prior or force
the re-ranker to reason about greetings, so they stay separate.

Order in the pipeline, and why it is exactly here
-------------------------------------------------
::

    ... -> SAFETY GATE (input) -> memory -> ROUTER -> retrieval -> generation

**After the safety gate**, so the gate is structurally untouchable: a blocked
turn returns from the orchestrator before this module is imported into the call
path at all. The router cannot make the crisis panel less likely to fire
because on a crisis turn the router never runs. :data:`Route.CRISIS` exists so
that a crisis turn is still *labelled* in telemetry and in the stored trace,
and the orchestrator sets it directly on the short-circuit path.

**After memory**, because a follow-up is only recognisable against what came
before. "aur kya" is not a question on its own, and the standalone query it
rewrites to ("further guidance on controlling anger after being scolded in
front of colleagues") is what makes retrieval possible for it at all.

Two stages
----------
1. **Rules** (:func:`rule_route`) - zero latency, zero cost, no model. A message
   of at most :data:`SHORT_MESSAGE_WORDS` words made up entirely of lexicon
   terms and fillers is decided here. Handles the whole greeting and
   acknowledgement bucket, which is half of the non-guidance traffic.
2. **Model** - one call to the cheap tier, temperature 0, hard timeout. Returns
   the route and the rewritten standalone query in one JSON object, so the
   follow-up rewrite costs no extra call.

Failure is **open, and never silent**: a timeout, a transport error, malformed
JSON or an unknown label all resolve to :data:`Route.GUIDANCE`, which retrieves
and answers fully. The failure mode of the router is "behaves like it did
before this phase", never "refuses to answer a real question". Every such
failure increments ``intent_router_fallback`` and logs at warning level.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app import telemetry
from app.config import PROJECT_ROOT, Settings, get_settings
from app.llm.base import TASK_ROUTE, LLMProvider
from app.pipeline.memory import MemoryContext

logger = logging.getLogger(__name__)


class Route(str, Enum):
    """What kind of conversational move the user just made."""

    GREETING = "greeting"
    META = "meta"
    ACK_OR_CLOSE = "ack_or_close"
    FOLLOWUP = "followup"
    GUIDANCE = "guidance"
    CRISIS = "crisis"
    OUT_OF_SCOPE = "out_of_scope"


#: The route a failed router produces. Chosen because it is the *most*
#: expensive and most complete behaviour, not the cheapest: a router that
#: breaks must degrade to answering the question, never to brushing it off.
DEFAULT_ROUTE = Route.GUIDANCE

#: Routes the model is allowed to choose. ``crisis`` is excluded on purpose -
#: it is the safety gate's verdict, not a language model's opinion, and a model
#: that could *choose* crisis could also choose not to.
MODEL_ROUTES: tuple[Route, ...] = (
    Route.GREETING,
    Route.META,
    Route.ACK_OR_CLOSE,
    Route.FOLLOWUP,
    Route.GUIDANCE,
    Route.OUT_OF_SCOPE,
)

#: Routes whose reply is built from retrieved verses.
RETRIEVING_ROUTES: frozenset[Route] = frozenset({Route.FOLLOWUP, Route.GUIDANCE})

#: Routes the zero-cost lexicon is permitted to decide. All four are messages
#: with no subject of their own, which is exactly what a word list can
#: recognise. ``guidance`` is excluded because deciding that something IS a
#: real request for help is a far stronger claim than deciding it is a
#: greeting; ``crisis`` because that is the gate's verdict and the gate has
#: already run. :meth:`Lexicon.load` raises on anything else.
LEXICON_ROUTES: frozenset[Route] = frozenset(
    {Route.GREETING, Route.ACK_OR_CLOSE, Route.META, Route.FOLLOWUP}
)

#: How a message that matches more than one route is resolved, most specific
#: first. The principle is that an actual request beats social framing: "hi who
#: are you" is a question with a greeting attached, and answering the greeting
#: would drop the question. Between the two purely social routes the opening
#: move wins, because "hi thanks" is still someone arriving.
#:
#: Every route in :data:`LEXICON_ROUTES` must appear here, or a message
#: matching only that route would fall off the end of the search.
LEXICON_PRECEDENCE: tuple[Route, ...] = (
    Route.META,
    Route.FOLLOWUP,
    Route.GREETING,
    Route.ACK_OR_CLOSE,
)

#: Stage 1 only decides messages at or below this length, or the length of the
#: longest phrase in the lexicon if that is longer (see
#: :attr:`Lexicon.longest_phrase_words`). A greeting is short; anything longer
#: is doing more than greeting even if it opens with "hi".
#:
#: The bound is secondary protection. The rule that actually keeps stage 1 safe
#: is whole-message consumption in :func:`rule_route`: every token must be a
#: lexicon phrase or a filler, so length alone never admits a message that says
#: something.
SHORT_MESSAGE_WORDS = 4


def _without_history_is_not_a_followup(
    route: Route, memory: MemoryContext | None
) -> Route:
    """Downgrade a follow-up with nothing to follow to guidance.

    Applied to **both** stages, which is the point of it being a function. The
    model produces this on first turns, and now the lexicon can too - "simpler"
    or "aur kya" as an opening message is not a request to simplify anything,
    and routing it to followup would send two words to the retriever and print
    a continuation of a conversation that has not happened.
    """
    if route is Route.FOLLOWUP and (memory is None or not memory.recent_turns):
        logger.info("router said followup with no history; treating as guidance")
        return Route.GUIDANCE
    return route


def _last_user_turn(memory: MemoryContext | None, current: str = "") -> str:
    """The person's most recent message **before** this one, or ``""``.

    ``current`` has to be excluded explicitly. The orchestrator writes the
    incoming message to the repository before the memory stage runs, so by the
    time memory is loaded the newest user turn in it *is* this turn - and
    without this, "the previous message" resolves to "aur kya" itself, which is
    the exact string that has no retrievable signal.
    """
    if memory is None:
        return ""
    current = current.strip()
    for role, content in reversed(memory.recent_turns):
        text = content.strip()
        if role == "user" and text and text != current:
            return text
    return ""

LEXICON_PATH = PROJECT_ROOT / "data" / "intent_lexicon.yaml"

_SPACE_RE = re.compile(r"\s+")

#: Unicode general-category initials that carry routing signal: letters (L*)
#: and combining marks (M*). Everything else - punctuation, digits, symbols,
#: emoji - is dropped before matching.
#:
#: Marks are the reason this is not simply ``[^\w\s]``. Python's ``\w`` matches
#: what ``str.isalnum()`` accepts, and a Devanagari matra is category Mn, which
#: is not alphanumeric - so a ``\w``-based strip turned "नमस्ते" into "न म स त",
#: four "words" of one consonant each. Matching still worked, because both the
#: lexicon and the message were mangled identically, but the **word count** did
#: not: a five-word Hindi phrase counted as seven, which silently widened the
#: stage-1 length gate for every message in every language. It also made the
#: Devanagari entries fragile, since stripping the vowel signs collapses words
#: that differ only by them.
_KEPT_CATEGORIES = ("L", "M")


class RouterDecisionStage(str, Enum):
    """Which layer produced the route - reported so the mix is measurable."""

    RULES = "rules"
    MODEL = "model"
    FALLBACK = "fallback"
    GATE = "gate"


@dataclass(frozen=True)
class RouteDecision:
    """The router's verdict for one turn, with everything needed to audit it."""

    route: Route = DEFAULT_ROUTE
    stage: RouterDecisionStage = RouterDecisionStage.FALLBACK
    #: The message rewritten as one self-contained English sentence, with
    #: pronouns and references to earlier turns resolved. Empty for routes that
    #: do not retrieve, and empty whenever the model did not supply one.
    standalone_query: str = ""
    #: Why the router decided as it did, in one human-readable clause.
    rationale: str = ""
    model: str = ""
    latency_ms: float = 0.0
    #: True when the model was asked and could not be used.
    fell_back: bool = False
    matched_terms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def retrieves(self) -> bool:
        return self.route in RETRIEVING_ROUTES

    def retrieval_query(self, message: str, memory: MemoryContext | None = None) -> str:
        """What retrieval should actually search for.

        A follow-up's own text ("aur kya", "simpler please") contains almost no
        retrievable signal, so something else has to stand in for it:

        1. the model's rewritten standalone query, when there is one;
        2. otherwise the person's **previous** message, which is the thing being
           followed up on. This is the case when the lexicon decided the
           follow-up at zero cost, so no model ran and no rewrite exists -
           without it, "simpler" would be sent to the retriever as a search
           term and would match nothing.

        A guidance message is already standalone, so its own words are kept
        here: they are the person's own vocabulary, and for the **fulltext**
        leg rewriting them can only lose information. The rewrite is applied to
        the vector leg instead - see :meth:`vector_query`.
        """
        if self.route is Route.FOLLOWUP:
            if self.standalone_query:
                return self.standalone_query
            previous = _last_user_turn(memory, current=message)
            if previous:
                return previous
        return message

    def vector_query(self, message: str, enabled: bool = True) -> str | None:
        """The text to embed for the vector leg, or ``None`` for "same as the query".

        Returning ``None`` rather than the message itself is deliberate: it lets
        the retriever leave its existing single-query path exactly as it was
        when there is no rewrite, so the flag being off is byte-identical to
        the behaviour before rewriting existed.

        The rewrite is English. The corpus is English. The measured gap it
        closes is that a Hinglish question scores 0.00 against an English
        corpus on the encoder in production while the same question in English
        scores 0.30 (section 14b).
        """
        if not enabled or not self.standalone_query:
            return None
        if self.standalone_query.strip() == message.strip():
            return None
        return self.standalone_query


# Stage 1 - rules
def _normalise(text: str) -> str:
    """Lowercase, strip punctuation/emoji/digits, collapse whitespace.

    Letters and combining marks survive in every script; see
    :data:`_KEPT_CATEGORIES` for why marks have to be named explicitly.

    >>> _normalise("Namaste!!! 🙏")
    'namaste'
    >>> _normalise("नमस्ते").split()
    ['नमस्ते']
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(
        character
        if character.isspace() or unicodedata.category(character)[0] in _KEPT_CATEGORIES
        else " "
        for character in folded
    )
    return _SPACE_RE.sub(" ", kept).strip()


def _collapse(text: str) -> str:
    """Collapse runs of a repeated character to one.

    Applied to **both** sides of every comparison, so it is a normalisation and
    not a fuzzy match: "hii" and "hi" both become "hi", and "hello" and "helloo"
    both become "helo". Nothing matches that would not have matched with the
    doubled letters spelled out in the lexicon.

    >>> _collapse("hiii")
    'hi'
    >>> _collapse("hello") == _collapse("helloo")
    True
    """
    return re.sub(r"(.)\1+", r"\1", text)


@dataclass(frozen=True)
class Lexicon:
    """Stage-1 terms, loaded from ``data/intent_lexicon.yaml``."""

    #: collapsed phrase -> route
    phrases: dict[str, Route]
    #: collapsed filler tokens
    fillers: frozenset[str]
    #: Longest phrase first, so "good morning" is tried before "morning".
    ordered: tuple[str, ...]
    #: Words in the longest phrase. The length gate uses this so that adding a
    #: five-word phrase permits a five-word message, without widening the gate
    #: for one-word greetings.
    longest_phrase_words: int = SHORT_MESSAGE_WORDS

    @property
    def max_message_words(self) -> int:
        return max(SHORT_MESSAGE_WORDS, self.longest_phrase_words)

    @classmethod
    def load(cls, path: Path | None = None) -> Lexicon:
        import yaml

        raw = yaml.safe_load((path or LEXICON_PATH).read_text(encoding="utf-8"))
        phrases: dict[str, Route] = {}
        for route_name, languages in (raw.get("routes") or {}).items():
            route = Route(route_name)
            if route not in LEXICON_ROUTES:
                # Refused rather than ignored. A file that could decide
                # `guidance` at zero cost, or `crisis` at all, is a safety
                # problem, and silently dropping the section would leave the
                # author believing it took effect.
                raise ValueError(
                    f"{route.value!r} may not be decided by the stage-1 lexicon; "
                    f"allowed: {sorted(item.value for item in LEXICON_ROUTES)}"
                )
            for terms in languages.values():
                for term in terms or ():
                    key = _collapse(_normalise(str(term)))
                    if key:
                        phrases.setdefault(key, route)
        fillers = frozenset(
            _collapse(_normalise(str(term))) for term in (raw.get("fillers") or ())
        )
        return cls(
            phrases=phrases,
            fillers=fillers,
            ordered=tuple(sorted(phrases, key=len, reverse=True)),
            longest_phrase_words=max((len(key.split()) for key in phrases), default=0),
        )

    @classmethod
    def empty(cls) -> Lexicon:
        return cls(phrases={}, fillers=frozenset(), ordered=())


_LEXICON: Lexicon | None = None


def get_lexicon() -> Lexicon:
    """Process-wide lexicon, read from disk once.

    A missing or unreadable file is not fatal: stage 1 simply decides nothing
    and every message goes to stage 2, which is slower and costs a call but is
    correct. Losing a convenience must not take the service down.
    """
    global _LEXICON
    if _LEXICON is None:
        try:
            _LEXICON = Lexicon.load()
            logger.info(
                "router lexicon loaded: %d phrases, %d fillers",
                len(_LEXICON.phrases),
                len(_LEXICON.fillers),
            )
        except Exception:
            logger.exception("router lexicon unavailable; stage 1 disabled")
            _LEXICON = Lexicon.empty()
    return _LEXICON


def rule_route(message: str, lexicon: Lexicon | None = None) -> RouteDecision | None:
    """Decide a subjectless message without a model, or return ``None``.

    The message must be **entirely** consumed by lexicon phrases and fillers,
    and must be at most :attr:`Lexicon.max_message_words` words. Containment is
    not enough and must never be: "hi i want to die" contains "hi".

    >>> rule_route("Namaste!").route            # doctest: +ELLIPSIS
    <Route.GREETING: 'greeting'>
    >>> rule_route("who are you").route
    <Route.META: 'meta'>
    >>> rule_route("hi i need help with my brother") is None
    True
    """
    lexicon = lexicon if lexicon is not None else get_lexicon()
    if not lexicon.phrases:
        return None

    normalised = _normalise(message)
    if not normalised or len(normalised.split()) > lexicon.max_message_words:
        return None

    remaining = _collapse(normalised)
    matched: list[str] = []
    routes: set[Route] = set()

    # Greedy longest-first consumption. Every token must be accounted for.
    changed = True
    while remaining and changed:
        changed = False
        for phrase in lexicon.ordered:
            if remaining == phrase or remaining.startswith(phrase + " "):
                routes.add(lexicon.phrases[phrase])
                matched.append(phrase)
                remaining = remaining[len(phrase) :].strip()
                changed = True
                break
        if changed:
            continue
        head, _, tail = remaining.partition(" ")
        if head in lexicon.fillers:
            remaining = tail.strip()
            changed = True

    if remaining or not routes:
        return None

    route = next(item for item in LEXICON_PRECEDENCE if item in routes)
    return RouteDecision(
        route=route,
        stage=RouterDecisionStage.RULES,
        rationale=f"matched the stage-1 lexicon entirely ({', '.join(matched)})",
        matched_terms=tuple(matched),
    )


# Stage 2 - the model
ROUTE_DEFINITIONS: dict[Route, str] = {
    Route.GREETING: "only saying hello or opening the conversation, asking nothing yet",
    Route.META: (
        "asking about GitaGuide itself - what it is, whether it is an AI, which edition "
        "of a text it uses, whether its verses can be trusted, or what a source text is"
    ),
    Route.ACK_OR_CLOSE: (
        "acknowledging or closing - agreeing, saying thanks, saying they understood, or "
        "saying goodbye, without asking anything new"
    ),
    Route.FOLLOWUP: (
        "continuing the previous exchange - asking for more, for an example, for a simpler "
        "or differently phrased version, or pushing back on what was just said. Only "
        "possible when there is a previous exchange"
    ),
    Route.GUIDANCE: (
        "bringing a real situation, problem, decision or feeling and wanting help with it. "
        "This is the default whenever there is any doubt"
    ),
    Route.OUT_OF_SCOPE: (
        "asking for something GitaGuide does not do - recipes, code, homework, news, "
        "translation, general trivia - with no personal situation behind it"
    ),
}


def build_route_prompt(message: str, memory: MemoryContext | None = None) -> str:
    """Strict single-JSON-object routing prompt for the cheap tier."""
    taxonomy = "\n".join(f"- {route.value}: {text}" for route, text in ROUTE_DEFINITIONS.items())

    history_block = "PREVIOUS EXCHANGE: none - this is the first message.\n"
    if memory is not None and memory.recent_turns:
        lines = [
            f"{role}: {content}"
            for role, content in memory.recent_turns[-4:]
        ]
        history_block = "PREVIOUS EXCHANGE (most recent last):\n" + "\n".join(lines) + "\n"

    return (
        f"TASK: {TASK_ROUTE}\n"
        "You are routing one message in a guidance conversation. Choose exactly one route "
        "and write a standalone query.\n\n"
        f"ROUTES:\n{taxonomy}\n\n"
        "Rules:\n"
        "- When in any doubt, choose guidance. Guidance is never the wrong choice for a "
        "message that describes a situation, however short.\n"
        "- followup requires a previous exchange. With none, a short message is guidance.\n"
        "- A message in Hindi, Hinglish, Gujarati or any other language is routed on what it "
        "MEANS, not on what language it is in.\n"
        "- standalone_query: rewrite the message as ONE clear English sentence describing the "
        "person's situation and what they are seeking, resolving anything that refers to an "
        "earlier turn. For greeting, meta, ack_or_close and out_of_scope, use an empty string.\n\n"
        'Return ONLY one-line minified JSON, no markdown and no explanation: '
        '{"route":"guidance","standalone_query":"..."}\n\n'
        f"{history_block}\n"
        f"MESSAGE: {message}\n"
    )


class ConversationRouter:
    """Two-stage router. Never raises; a broken router routes to guidance.

    Examples
    --------
    >>> import asyncio
    >>> asyncio.run(ConversationRouter(llm=None).route("hello")).route
    <Route.GREETING: 'greeting'>
    >>> asyncio.run(ConversationRouter(llm=None).route("my boss took credit")).route
    <Route.GUIDANCE: 'guidance'>
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        settings: Settings | None = None,
        lexicon: Lexicon | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm
        self.lexicon = lexicon

    async def route(
        self, message: str, memory: MemoryContext | None = None
    ) -> RouteDecision:
        """Route one message. Returns a decision for every input, always."""
        started = time.perf_counter()

        decided = rule_route(message, self.lexicon if self.lexicon is not None else None)
        if decided is not None:
            route = _without_history_is_not_a_followup(decided.route, memory)
            return RouteDecision(
                route=route,
                stage=decided.stage,
                rationale=decided.rationale,
                matched_terms=decided.matched_terms,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        if self.llm is None:
            return RouteDecision(
                route=DEFAULT_ROUTE,
                stage=RouterDecisionStage.FALLBACK,
                rationale="no router model configured; defaulted to guidance",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        return await self._model_route(message, memory, started)

    async def _model_route(
        self, message: str, memory: MemoryContext | None, started: float
    ) -> RouteDecision:
        """One bounded model call, with every failure resolving to guidance."""

        def fallback(reason: str, model: str = "") -> RouteDecision:
            # COUNTER intent_router_fallback: the model was asked and could not
            # be used - timeout, transport, bad JSON, unknown label. The turn is
            # still answered as guidance, so this is a quality signal, not an
            # error rate; if it climbs, set GITAGUIDE_ROUTER_MODEL_ENABLED=false.
            telemetry.count("intent_router_fallback")
            logger.warning("router fell back to %s: %s", DEFAULT_ROUTE.value, reason)
            return RouteDecision(
                route=DEFAULT_ROUTE,
                stage=RouterDecisionStage.FALLBACK,
                rationale=reason,
                model=model,
                fell_back=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

        try:
            response = await asyncio.wait_for(
                self.llm.complete(
                    build_route_prompt(message, memory),
                    max_tokens=self.settings.router_max_tokens,
                    temperature=0.0,
                ),
                timeout=self.settings.router_timeout_seconds,
            )
        except TimeoutError:
            return fallback(
                f"router timed out after {self.settings.router_timeout_seconds}s"
            )
        except asyncio.CancelledError:
            # The caller went away. Not a router failure and not ours to
            # swallow: a BaseException that must keep propagating.
            raise
        except Exception as exc:
            return fallback(f"router call failed: {type(exc).__name__}: {exc}")

        payload = self._parse(response.text)
        if payload is None:
            return fallback("router returned unparseable JSON", response.model)

        label = str(payload.get("route", "")).strip().lower()
        try:
            route = Route(label)
        except ValueError:
            return fallback(f"router returned unknown route {label!r}", response.model)
        if route not in MODEL_ROUTES:
            return fallback(f"router returned reserved route {label!r}", response.model)

        # A follow-up with no history to follow is a contradiction; the model
        # occasionally produces one on a first turn. Guidance is the honest read.
        route = _without_history_is_not_a_followup(route, memory)

        standalone = str(payload.get("standalone_query", "") or "").strip()
        return RouteDecision(
            route=route,
            stage=RouterDecisionStage.MODEL,
            standalone_query=standalone if route in RETRIEVING_ROUTES else "",
            rationale="classified by the router model",
            model=response.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    @staticmethod
    def _parse(text: str) -> dict[str, Any] | None:
        """Parse the model's JSON, tolerating a code fence or surrounding prose."""
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-z]*\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", candidate, re.DOTALL)
            if match is None:
                return None
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return parsed if isinstance(parsed, dict) else None
