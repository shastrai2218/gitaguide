"""Tests for Stage 4c Strategy Selection (Phase 3 Scripture Relevance)."""


from app.pipeline.memory import MemoryContext
from app.pipeline.router import Route
from app.pipeline.schemas import IntentLabel, IntentResult, TopicContext, TopicRecord
from app.pipeline.strategy import ScriptureStrategy, resolve_strategy
from app.pipeline.tiers import GENERAL_ASSISTANCE_TIER, GUIDANCE_TIER, PRACTICAL_GUIDANCE_TIER


def _intent(label: IntentLabel) -> IntentResult:
    return IntentResult(intent=label, confidence=0.9, scores={label.value: 0.9})


def test_greeting_routes_to_practical() -> None:
    strat, tier = resolve_strategy("hello there", Route.GREETING, _intent(IntentLabel.OTHER))
    assert strat == ScriptureStrategy.PRACTICAL
    assert tier.max_verses == 0


def test_general_assistance_checklist_routes_to_general_assistance() -> None:
    msg = "Can you help me prepare a checklist and meeting agenda for my team?"
    strat, tier = resolve_strategy(msg, Route.GUIDANCE, _intent(IntentLabel.OTHER))
    assert strat == ScriptureStrategy.GENERAL_ASSISTANCE
    assert tier == GENERAL_ASSISTANCE_TIER
    assert tier.max_verses == 0


def test_acute_heartbreak_venting_routes_to_practical() -> None:
    msg = "She cheated on me after 4 years of relationship and I can't stop crying."
    strat, tier = resolve_strategy(msg, Route.GUIDANCE, _intent(IntentLabel.EMOTIONAL_SUPPORT))
    assert strat == ScriptureStrategy.PRACTICAL
    assert tier == PRACTICAL_GUIDANCE_TIER
    assert tier.max_verses == 0


def test_multi_topic_ambiguity_routes_to_practical() -> None:
    topic1 = TopicRecord(topic_id="t1", name="Finance", category="financial")
    topic2 = TopicRecord(topic_id="t2", name="Relationship", category="relationship")
    topic_ctx = TopicContext(active_topics=[topic1, topic2], is_multi_topic=True)
    mem = MemoryContext(session_id="s1", topic_context=topic_ctx)

    msg = "i m stressed and don't know what should i do now"
    strat, tier = resolve_strategy(msg, Route.GUIDANCE, _intent(IntentLabel.EMOTIONAL_SUPPORT), memory=mem)
    assert strat == ScriptureStrategy.PRACTICAL
    assert tier == PRACTICAL_GUIDANCE_TIER
    assert tier.max_verses == 0


def test_explicit_scripture_request_routes_to_scripture_grounded() -> None:
    msg = "What does the Bhagavad Gita say in chapter 2 verse 47?"
    strat, tier = resolve_strategy(msg, Route.GUIDANCE, _intent(IntentLabel.SCRIPTURE_QUESTION))
    assert strat == ScriptureStrategy.SCRIPTURE_GROUNDED
    assert tier == GUIDANCE_TIER


def test_ethical_and_svadharma_dilemma_routes_to_hybrid() -> None:
    msg = "I am facing a serious conflict of interest between my duty to my family and my professional ethics as an auditor."
    strat, tier = resolve_strategy(msg, Route.GUIDANCE, _intent(IntentLabel.PROFESSIONAL_DILEMMA))
    assert strat == ScriptureStrategy.HYBRID
    assert tier == GUIDANCE_TIER
