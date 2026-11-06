"""Stage 4c - Scripture & Response Strategy Selection.

Decides whether the current turn requires:
A. Practical response: No scripture needed (listening, emotional validation, practical steps, or multi-topic clarification).
B. Scripture-grounded response: Direct scripture question or exegetical lookup.
C. Hybrid response: Practical guidance grounded in relevant scripture.
D. General assistance: Everyday queries (productivity, drafting, general knowledge).
"""

from __future__ import annotations

import re
from enum import Enum

from app.pipeline.memory import MemoryContext
from app.pipeline.router import Route
from app.pipeline.schemas import IntentLabel, IntentResult
from app.pipeline.tiers import (
    GENERAL_ASSISTANCE_TIER,
    GUIDANCE_TIER,
    PRACTICAL_GUIDANCE_TIER,
    ReplyTier,
    tier_for,
)


class ScriptureStrategy(str, Enum):
    PRACTICAL = "practical"
    HYBRID = "hybrid"
    SCRIPTURE_GROUNDED = "scripture_grounded"
    GENERAL_ASSISTANCE = "general_assistance"


#: Everyday general assistance, productivity, writing, or operational tasks
GENERAL_ASSISTANCE_PATTERNS = re.compile(
    r"\b(?:checklist|draft(?:ing)?|email|routine|schedule|pomodoro|mutual fund|"
    r"80/20|pareto|exercise|diet plan|agenda|decision matrix|active listening|"
    r"morning routine|write a letter|meeting agenda|prep plan|roadmap|steps to take|"
    r"study plan|productivity|resume|interview tips|budget template)\b",
    re.IGNORECASE,
)

#: Ambiguous follow-ups under high multi-topic stress
AMBIGUOUS_STRESS_PATTERNS = re.compile(
    r"\b(?:i(?:'m| m) (?:stressed|stressed out|overwhelmed|anxious|lost)|"
    r"what should i do(?: now)?|help me|everything is too much|don'?t know what (?:to|should i) do)\b",
    re.IGNORECASE,
)

#: Truly explicit scripture queries requesting shlokas, verses, or specific text lookups
EXPLICIT_SCRIPTURE_REQUEST_PATTERNS = re.compile(
    r"\b(?:(?:what does (?:the )?|tell me (?:what )?(?:the )?)(?:gita|bhagavad|arthashastra|shastra|scripture) says?|"
    r"tell me a (?:shloka|slok|verse)|quote (?:a |the )?verse|which (?:shloka|slok|verse)|"
    r"sanskrit (?:shloka|slok|verse)|explain (?:chapter|verse|shloka) \d+|karmanye vadhikaraste|"
    r"in the bhagavad gita|in the arthashastra)\b",
    re.IGNORECASE,
)

#: Ethical dilemmas, duty, svadharma, leadership ethics, moral crossroads (legitimate Hybrid wisdom)
ETHICAL_OR_DUTY_PATTERNS = re.compile(
    r"\b(?:duty|dharma|svadharma|ethics|ethical|morals?|moral dilemma|right thing to do|"
    r"conflict of interest|righteousness|leadership ethics|philosophy of|what is karma|"
    r"nishkama|whistleblowing|duty to family|duty to country)\b",
    re.IGNORECASE,
)

#: System metadata and capability inquiries
SYSTEM_META_PATTERNS = re.compile(
    r"\b(?:what can you help|who are you|are you an? ai|are you human|do you have feelings|"
    r"why are you called|what is gitaguide|how do you work|what books? do you have)\b",
    re.IGNORECASE,
)

#: Acute personal disclosures, panic, immediate emotional overwhelm, operational distress
ACUTE_VENTING_OR_OPERATIONAL_PATTERNS = re.compile(
    r"\b(?:she cheated|he cheated|cheated on me|broke up with me|dumped me|"
    r"crying|can't stop crying|sobbing|panic attack|panicking|heartbroken|"
    r"how do i calm down immediately|my father took loan|stuck payment|pay back the loan|"
    r"money is stucked|not paying us|unpaid invoice|loan of 1 cr|"
    r"how do i cancel|steps to apply|procedure for|documents needed)\b",
    re.IGNORECASE,
)


def resolve_strategy(
    message: str,
    route: Route,
    intent: IntentResult,
    memory: MemoryContext | None = None,
) -> tuple[ScriptureStrategy, ReplyTier]:
    """Dynamically determine the scripture strategy and select the appropriate ReplyTier."""
    # 1. Non-guidance routes (greetings, meta, acks, out of scope) use existing conversational tiers
    if route in {Route.GREETING, Route.META, Route.ACK_OR_CLOSE, Route.OUT_OF_SCOPE}:
        return ScriptureStrategy.PRACTICAL, tier_for(route)

    # 2. System capability / meta questions
    if SYSTEM_META_PATTERNS.search(message):
        return ScriptureStrategy.PRACTICAL, tier_for(Route.META)

    # 3. Direct scripture questions always get scripture-grounded tier
    if intent.intent == IntentLabel.SCRIPTURE_QUESTION or EXPLICIT_SCRIPTURE_REQUEST_PATTERNS.search(message):
        return ScriptureStrategy.SCRIPTURE_GROUNDED, GUIDANCE_TIER

    # 4. Everyday general assistance queries (productivity, checklists, explanations)
    if GENERAL_ASSISTANCE_PATTERNS.search(message) and not EXPLICIT_SCRIPTURE_REQUEST_PATTERNS.search(message):
        return ScriptureStrategy.GENERAL_ASSISTANCE, GENERAL_ASSISTANCE_TIER

    # 5. Multi-topic ambiguity: user says "i m stressed" with multiple active topics in memory
    if memory and memory.topic_context and memory.topic_context.is_multi_topic:
        if AMBIGUOUS_STRESS_PATTERNS.search(message):
            return ScriptureStrategy.PRACTICAL, PRACTICAL_GUIDANCE_TIER

    # 6. Specific acute personal heartbreak/loan disclosures or acute venting where practical empathy comes first
    if ACUTE_VENTING_OR_OPERATIONAL_PATTERNS.search(message) and not EXPLICIT_SCRIPTURE_REQUEST_PATTERNS.search(message):
        return ScriptureStrategy.PRACTICAL, PRACTICAL_GUIDANCE_TIER

    # 7. Ethical, moral, duty (svadharma), leadership dilemmas: Hybrid guidance grounded in scripture
    if ETHICAL_OR_DUTY_PATTERNS.search(message):
        return ScriptureStrategy.HYBRID, GUIDANCE_TIER

    # 8. Default for reflective life dilemmas: Hybrid tier
    return ScriptureStrategy.HYBRID, GUIDANCE_TIER
