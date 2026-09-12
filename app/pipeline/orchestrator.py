"""Stage 0 - the orchestrator that chains every other stage.

Research-pipeline role
---------------------
This is the canonical description of the GitaGuide pipeline, in code::

    ensure session
      -> emotion detection            (app.pipeline.emotion)
      -> intent classification        (app.pipeline.intent)
      -> SAFETY GATE (input)          (app.pipeline.safety)   <-- short-circuits
      -> memory load                  (app.pipeline.memory)
      -> conversational routing       (app.pipeline.router)   <-- may skip retrieval
      -> hybrid retrieval             (app.pipeline.retrieval)
      -> grounded generation          (app.pipeline.generation)
      -> SAFETY GATE (output)         (app.pipeline.safety)
      -> persist turn + refresh rolling summary

Rule 4 is enforced structurally: :meth:`GuidanceOrchestrator.handle_turn`
returns before ``retrieve``/``generate`` are ever called when the input gate
blocks, and the output check happens after generation but before the answer is
returned or stored.

The routing stage (added 13 Sep 2026, Phase 1b)
-----------------------------------------------
One stage was inserted, and its position is the whole of its safety argument.

It sits **after the safety gate**, so a crisis turn has already returned before
the router exists in the call path: routing cannot make the crisis panel less
likely to fire, because on a crisis turn it does not run. And it sits **after
memory**, because a follow-up ("aur kya", "simpler please") is only
recognisable against the turns before it, and the standalone query it rewrites
to is what makes such a turn retrievable at all.

What it changes downstream is that ``retrieve`` and the grounded path are no
longer unconditional: a greeting, an acknowledgement, a question about
GitaGuide itself or an out-of-scope ask are answered by
:meth:`GroundedGenerator.generate_conversational`, with no retrieval, no
citations and a much shorter budget. Before this, every one of those got a
verse it never asked for. The output safety gate still runs on all of them -
it is outside the branch, not inside it.

Everything it depends on is injected, so the same class runs fully offline
(fakes) or fully online (Neo4j + PostgreSQL + Anthropic) with no code change.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from app.config import Settings, get_settings
from app.db.repository import (
    InMemoryRepository,
    MessageRecord,
    Repository,
    SafetyLogRecord,
)
from app.embeddings import Embedder, build_embedder
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider, build_router_llm
from app.pipeline.emotion import EmotionDetector
from app.pipeline.generation import GroundedGenerator
from app.pipeline.intent import IntentClassifier
from app.pipeline.memory import ConversationMemory
from app.pipeline.retrieval import HybridRetriever, build_retriever
from app.pipeline.router import ConversationRouter, Route, RouteDecision, RouterDecisionStage
from app.pipeline.safety import SafetyGate, SafetyVerdict
from app.pipeline.schemas import (
    EmotionResult,
    GenerationResult,
    GuidanceResult,
    IntentResult,
    MemoryTrace,
    RetrievalTrace,
    RouteTrace,
    UserTurn,
)
from app.pipeline.tiers import tier_for

logger = logging.getLogger(__name__)

STAGE_EMOTION = "emotion"
STAGE_INTENT = "intent"
STAGE_SAFETY_INPUT = "safety_input"
STAGE_MEMORY = "memory"
STAGE_ROUTER = "router"
STAGE_RETRIEVAL = "retrieval"
STAGE_GENERATION = "generation"
STAGE_SAFETY_OUTPUT = "safety_output"
STAGE_PERSIST = "persist"


class StageTimer:
    """Wall-clock per pipeline stage, recorded under the ``STAGE_*`` names.

    Latency here is not a micro-optimisation concern - it is the difference
    between a reply that feels considered and one that feels broken. The turn
    total alone cannot say *which* stage spent it, and the answer is not always
    generation: a cold ONNX session, a slow Neo4j query, or a NIM 503 retry all
    show up as "the app is slow" without this.

    Reuses ``stages_run``'s vocabulary so the two lists cannot drift apart. Use
    it as a context manager per stage::

        with timer(STAGE_RETRIEVAL):
            ...

    It records the stage even when the body raises, so a failed turn still says
    where the time went.
    """

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self._stage: str | None = None
        self._started: float = 0.0

    def __call__(self, stage: str) -> StageTimer:
        self._stage = stage
        return self

    def __enter__(self) -> StageTimer:
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._stage is not None:
            elapsed = round((time.perf_counter() - self._started) * 1000, 2)
            self.timings[self._stage] = round(self.timings.get(self._stage, 0.0) + elapsed, 2)
            self._stage = None


class GuidanceOrchestrator:
    """Chains the pipeline and returns a fully traced :class:`GuidanceResult`."""

    def __init__(
        self,
        detector: EmotionDetector,
        intent_classifier: IntentClassifier,
        safety_gate: SafetyGate,
        retriever: HybridRetriever,
        generator: GroundedGenerator,
        memory: ConversationMemory,
        repository: Repository,
        settings: Settings | None = None,
        router: ConversationRouter | None = None,
    ) -> None:
        self.detector = detector
        self.intent_classifier = intent_classifier
        self.safety_gate = safety_gate
        self.retriever = retriever
        self.generator = generator
        self.memory = memory
        self.repository = repository
        self.settings = settings or get_settings()
        #: Optional so that an orchestrator assembled by hand in a test keeps
        #: working unchanged; with no router every turn routes to guidance,
        #: which is exactly the pre-Phase-1b behaviour.
        self.router = router or ConversationRouter(llm=None, settings=self.settings)

    # ------------------------------------------------------------------ main
    async def handle_turn(self, turn: UserTurn) -> GuidanceResult:
        """Run one full turn of the pipeline."""
        started = time.perf_counter()
        stages: list[str] = []
        timer = StageTimer()

        session = await self.repository.ensure_session(turn.session_id, turn.locale)
        message = turn.message.strip()

        # ---- stage 1: emotion (CPU-bound; off the event loop) -------------
        with timer(STAGE_EMOTION):
            emotion: EmotionResult = await asyncio.to_thread(self.detector.detect, message)
        stages.append(STAGE_EMOTION)

        # ---- stage 2: intent ---------------------------------------------
        with timer(STAGE_INTENT):
            intent: IntentResult = await self.intent_classifier.classify(message, emotion)
        stages.append(STAGE_INTENT)

        # ---- stage 3: SAFETY GATE (before retrieval/generation) ----------
        with timer(STAGE_SAFETY_INPUT):
            input_verdict = self.safety_gate.check_input(message, emotion, intent, session.id)
        stages.append(STAGE_SAFETY_INPUT)

        user_message = await self.repository.add_message(
            MessageRecord(
                id=str(uuid.uuid4()),
                session_id=session.id,
                role="user",
                content=message,
                emotion=emotion.model_dump(mode="json"),
                intent=intent.model_dump(mode="json"),
                safety=input_verdict.model_dump(mode="json"),
            )
        )
        await self._persist_safety(input_verdict, session.id, user_message.id, "input", emotion, intent)

        if input_verdict.blocked:
            return await self._crisis_short_circuit(
                session_id=session.id,
                turn_count=session.turn_count,
                emotion=emotion,
                intent=intent,
                verdict=input_verdict,
                stages=stages,
                started=started,
                timer=timer,
            )

        # ---- stage 4: memory ---------------------------------------------
        with timer(STAGE_MEMORY):
            memory_context = await self.memory.load(session.id)
            memory_trace = memory_context.trace()
        stages.append(STAGE_MEMORY)

        # ---- stage 4b: conversational routing ----------------------------
        # After the gate (so crisis is already handled) and after memory (so a
        # follow-up has something to follow).
        with timer(STAGE_ROUTER):
            decision: RouteDecision = await self.router.route(message, memory_context)
        stages.append(STAGE_ROUTER)
        tier = tier_for(decision.route)

        # ---- stage 5: hybrid retrieval -----------------------------------
        # Conditional now. A greeting does not get a verse it never asked for.
        retrieval: RetrievalTrace | None = None
        if tier.retrieves:
            with timer(STAGE_RETRIEVAL):
                retrieval = await self.retriever.retrieve(
                    decision.retrieval_query(message), emotion, intent
                )
            stages.append(STAGE_RETRIEVAL)

        # ---- stage 6: generation -----------------------------------------
        with timer(STAGE_GENERATION):
            if retrieval is None:
                generation: GenerationResult = await self.generator.generate_conversational(
                    message, tier, memory_context
                )
            else:
                generation = await self.generator.generate(
                    message, retrieval, emotion, intent, memory_context, tier
                )
        stages.append(STAGE_GENERATION)

        # ---- stage 7: SAFETY GATE (output) -------------------------------
        with timer(STAGE_SAFETY_OUTPUT):
            output_verdict = self.safety_gate.check_output(
                generation.answer, session.id, input_verdict
            )
        stages.append(STAGE_SAFETY_OUTPUT)
        answer = (
            self.safety_gate.crisis_response(output_verdict)
            if output_verdict.blocked
            else generation.answer
        )

        # ---- stage 8: persist + memory refresh ---------------------------
        # The row stores the timings for the stages that *produced* it, the same
        # cut as its own ``latency_ms``: the persist figure cannot be inside the
        # row that the persisting writes. It is measured, logged and returned.
        with timer(STAGE_PERSIST):
            assistant_message = await self.repository.add_message(
                MessageRecord(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    role="assistant",
                    content=answer,
                    emotion=emotion.model_dump(mode="json"),
                    intent=intent.model_dump(mode="json"),
                    safety=output_verdict.model_dump(mode="json"),
                    retrieval=retrieval.model_dump(mode="json") if retrieval else {},
                    generation=generation.model_dump(mode="json"),
                    memory=memory_trace.model_dump(mode="json"),
                    timings=dict(timer.timings),
                    citations=list(generation.citations),
                    crisis=False,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            )
            await self._persist_safety(
                output_verdict, session.id, assistant_message.id, "output", emotion, intent
            )
            summary = await self.memory.maybe_update_summary(session.id)
        stages.append(STAGE_PERSIST)
        self._log_timings(session.id, assistant_message.id, timer.timings, crisis=False)

        return GuidanceResult(
            session_id=session.id,
            message_id=assistant_message.id,
            answer=answer,
            crisis=False,
            emotion=emotion,
            intent=intent,
            safety=input_verdict,
            output_safety=output_verdict,
            retrieval=retrieval,
            generation=generation,
            memory=memory_trace,
            route=RouteTrace.from_decision(decision),
            timings=dict(timer.timings),
            memory_summary=summary or memory_context.summary,
            stages_run=stages,
            total_latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    # ------------------------------------------------------------- helpers
    async def _crisis_short_circuit(
        self,
        session_id: str,
        turn_count: int,
        emotion: EmotionResult,
        intent: IntentResult,
        verdict: SafetyVerdict,
        stages: list[str],
        started: float,
        timer: StageTimer,
    ) -> GuidanceResult:
        """Return the crisis response without touching retrieval or the LLM.

        The memory stage sits *after* the gate, so it genuinely does not run
        here. The trace says so: ``loaded=False`` with nothing injected. The
        session's ``turn_count`` is still reported because it is a fact about
        the session, not a claim about what reached the prompt.

        The router sits after memory, so it does not run here either. The route
        is recorded as ``crisis`` at stage ``gate``, which is the literal truth:
        the gate decided it, no model was asked, and no lexicon was consulted.
        That distinction matters if routing is ever blamed for a crisis turn -
        the trace can prove the router was never in the path.
        """
        answer = self.safety_gate.crisis_response(verdict)
        memory_trace = MemoryTrace(turn_count=turn_count, loaded=False)
        with timer(STAGE_PERSIST):
            assistant_message = await self.repository.add_message(
                MessageRecord(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=answer,
                    emotion=emotion.model_dump(mode="json"),
                    intent=intent.model_dump(mode="json"),
                    safety=verdict.model_dump(mode="json"),
                    memory=memory_trace.model_dump(mode="json"),
                    timings=dict(timer.timings),
                    crisis=True,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            )
        logger.info(
            "crisis short-circuit: session=%s categories=%s", session_id, verdict.categories
        )
        self._log_timings(session_id, assistant_message.id, timer.timings, crisis=True)
        return GuidanceResult(
            session_id=session_id,
            message_id=assistant_message.id,
            answer=answer,
            crisis=True,
            emotion=emotion,
            intent=intent,
            safety=verdict,
            retrieval=None,
            generation=None,
            memory=memory_trace,
            route=RouteTrace(
                route=Route.CRISIS.value,
                stage=RouterDecisionStage.GATE.value,
                rationale="the safety gate blocked this turn; the router never ran",
            ),
            timings=dict(timer.timings),
            stages_run=stages,
            total_latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    @staticmethod
    def _log_timings(
        session_id: str, message_id: str, timings: dict[str, float], *, crisis: bool
    ) -> None:
        """One structured line per turn: where the wall clock went.

        No metrics service and no new dependency - the requirement is to be able
        to answer "which stage was slow on that turn?" from the logs a tester's
        run already produced.
        """
        if not timings:
            return
        total = round(sum(timings.values()), 2)
        breakdown = " ".join(f"{stage}={value:.1f}ms" for stage, value in timings.items())
        logger.info(
            "TURN_LATENCY session=%s message=%s crisis=%s total=%.1fms %s",
            session_id,
            message_id,
            crisis,
            total,
            breakdown,
        )

    async def _persist_safety(
        self,
        verdict: SafetyVerdict,
        session_id: str,
        message_id: str,
        stage: str,
        emotion: EmotionResult,
        intent: IntentResult,
    ) -> None:
        """Write triggers to ``safety_logs``.

        The gate's own sink is synchronous (stdlib logging), so database
        persistence happens here, where we are already in async context.
        """
        if not verdict.requires_audit() or not self.settings.safety_log_enabled:
            return
        await self.repository.log_safety(
            SafetyLogRecord(
                id=str(uuid.uuid4()),
                session_id=session_id,
                message_id=message_id,
                stage=stage,
                blocked=verdict.blocked,
                layers=verdict.audit_layers(),
                categories=verdict.audit_categories(),
                triggers={
                    "items": [trigger.model_dump(mode="json") for trigger in verdict.triggers],
                    "signals": [signal.model_dump(mode="json") for signal in verdict.signals],
                },
                excerpt="",
                dominant_emotion=emotion.dominant,
                emotion_intensity=emotion.intensity,
                intent=intent.intent.value,
            )
        )

    async def explain(self, message_id: str) -> MessageRecord | None:
        """Stored trace for ``GET /explain/{message_id}``."""
        return await self.repository.get_message(message_id)

    async def health(self) -> dict[str, Any]:
        """Component-level health used by ``GET /health``."""
        return {
            "emotion_backend": self.detector.backend.name,
            "retrieval_backend": self.retriever.backend,
            "llm_provider": self.generator.llm.name,
            "repository": self.repository.backend,
            "database_ok": await self.repository.health(),
            "mock_mode": self.settings.mock_mode,
        }

    def warmup(self) -> None:
        """Materialise lazy resources (called from the FastAPI lifespan)."""
        self.detector.warmup()
        self.retriever.warmup()

    async def aclose(self) -> None:
        await self.retriever.aclose()
        await self.generator.llm.aclose()
        await self.repository.aclose()


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def build_orchestrator(
    settings: Settings | None = None,
    repository: Repository | None = None,
    llm: LLMProvider | None = None,
    retriever: HybridRetriever | None = None,
    embedder: Embedder | None = None,
    detector: EmotionDetector | None = None,
) -> GuidanceOrchestrator:
    """Assemble the pipeline from settings, accepting overrides for tests.

    Defaults are chosen so that ``build_orchestrator()`` with no arguments and
    no ``.env`` file yields a completely offline, working system.
    """
    settings = settings or get_settings()
    repository = repository or InMemoryRepository()
    llm = llm or build_llm_provider(settings)
    embedder = embedder or build_embedder(settings)
    retriever = retriever or build_retriever(settings, embedder)
    detector = detector or EmotionDetector(settings)

    return GuidanceOrchestrator(
        detector=detector,
        intent_classifier=IntentClassifier(llm=llm, settings=settings),
        safety_gate=SafetyGate(settings=settings),
        retriever=retriever,
        generator=GroundedGenerator(llm=llm, settings=settings),
        memory=ConversationMemory(repository=repository, llm=llm, settings=settings),
        repository=repository,
        settings=settings,
        router=ConversationRouter(llm=build_router_llm(settings), settings=settings),
    )
