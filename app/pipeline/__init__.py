"""The GitaGuide pipeline, one module per research stage.

    emotion  -> intent -> safety (input) -> memory -> retrieval
             -> generation -> safety (output)

:mod:`app.pipeline.orchestrator` chains them; every module also runs standalone
with an offline backend so stages can be studied in isolation.
"""

from app.pipeline.emotion import EmotionDetector
from app.pipeline.generation import GroundedGenerator
from app.pipeline.intent import IntentClassifier
from app.pipeline.memory import ConversationMemory, MemoryContext
from app.pipeline.orchestrator import GuidanceOrchestrator, build_orchestrator
from app.pipeline.retrieval import HybridRetriever, build_retriever
from app.pipeline.safety import SafetyGate
from app.pipeline.schemas import (
    EmotionResult,
    GenerationResult,
    GuidanceResult,
    IntentLabel,
    IntentResult,
    RetrievalTrace,
    SafetyVerdict,
    UserTurn,
)

__all__ = [
    "ConversationMemory",
    "EmotionDetector",
    "EmotionResult",
    "GenerationResult",
    "GroundedGenerator",
    "GuidanceOrchestrator",
    "GuidanceResult",
    "HybridRetriever",
    "IntentClassifier",
    "IntentLabel",
    "IntentResult",
    "MemoryContext",
    "RetrievalTrace",
    "SafetyGate",
    "SafetyVerdict",
    "UserTurn",
    "build_orchestrator",
    "build_retriever",
]
