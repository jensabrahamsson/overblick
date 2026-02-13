"""
Capabilities — composable behavioral building blocks for agent plugins.

Bundles:
    psychology  = [dream, therapy, emotional]
    knowledge   = [learning, loader]
    social      = [openings]
    engagement  = [analyzer, composer]
    conversation = [conversation_tracker]
    content     = [summarizer]
"""

from blick.capabilities.psychology.dream import DreamCapability
from blick.capabilities.psychology.therapy import TherapyCapability
from blick.capabilities.psychology.emotional import EmotionalCapability
from blick.capabilities.knowledge.learning import LearningCapability
from blick.capabilities.knowledge.loader import KnowledgeCapability
from blick.capabilities.social.openings import OpeningCapability
from blick.capabilities.engagement.analyzer import AnalyzerCapability
from blick.capabilities.engagement.composer import ComposerCapability
from blick.capabilities.conversation.tracker import ConversationCapability
from blick.capabilities.content.summarizer import SummarizerCapability

# Name -> class mapping for registry
CAPABILITY_REGISTRY: dict[str, type] = {
    "dream_system": DreamCapability,
    "therapy_system": TherapyCapability,
    "emotional_state": EmotionalCapability,
    "safe_learning": LearningCapability,
    "knowledge_loader": KnowledgeCapability,
    "openings": OpeningCapability,
    "analyzer": AnalyzerCapability,
    "composer": ComposerCapability,
    "conversation_tracker": ConversationCapability,
    "summarizer": SummarizerCapability,
}

# Bundle -> capability names
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    "psychology": ["dream_system", "therapy_system", "emotional_state"],
    "knowledge": ["safe_learning", "knowledge_loader"],
    "social": ["openings"],
    "engagement": ["analyzer", "composer"],
    "conversation": ["conversation_tracker"],
    "content": ["summarizer"],
}


def resolve_capabilities(names: list[str]) -> list[str]:
    """Resolve capability names, expanding bundles to individual capabilities."""
    resolved = []
    for name in names:
        if name in CAPABILITY_BUNDLES:
            for cap_name in CAPABILITY_BUNDLES[name]:
                if cap_name not in resolved:
                    resolved.append(cap_name)
        elif name in CAPABILITY_REGISTRY:
            if name not in resolved:
                resolved.append(name)
    return resolved


__all__ = [
    "DreamCapability",
    "TherapyCapability",
    "EmotionalCapability",
    "LearningCapability",
    "KnowledgeCapability",
    "OpeningCapability",
    "AnalyzerCapability",
    "ComposerCapability",
    "ConversationCapability",
    "SummarizerCapability",
    "CAPABILITY_REGISTRY",
    "CAPABILITY_BUNDLES",
    "resolve_capabilities",
]
