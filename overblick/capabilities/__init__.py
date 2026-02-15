"""
Capabilities — composable behavioral building blocks for agent plugins.

Bundles:
    psychology     = [dream, therapy, emotional] — DEPRECATED: Use psychological_framework in personality.yaml
    knowledge      = [learning, loader]
    social         = [openings]
    engagement     = [analyzer, composer]
    conversation   = [conversation_tracker]
    content        = [summarizer]
    speech         = [stt, tts]
    vision         = [vision]
    communication  = [boss_request, email, gmail]
    monitoring     = [host_inspection]
"""

from overblick.capabilities.psychology.dream import DreamCapability
from overblick.capabilities.psychology.therapy import TherapyCapability
from overblick.capabilities.psychology.emotional import EmotionalCapability
from overblick.capabilities.knowledge.learning import LearningCapability
from overblick.capabilities.knowledge.loader import KnowledgeCapability
from overblick.capabilities.social.openings import OpeningCapability
from overblick.capabilities.engagement.analyzer import AnalyzerCapability
from overblick.capabilities.engagement.composer import ComposerCapability
from overblick.capabilities.conversation.tracker import ConversationCapability
from overblick.capabilities.content.summarizer import SummarizerCapability
from overblick.capabilities.speech.stt import SpeechToTextCapability
from overblick.capabilities.speech.tts import TextToSpeechCapability
from overblick.capabilities.vision.analyzer import VisionCapability
from overblick.capabilities.communication.boss_request import BossRequestCapability
from overblick.capabilities.communication.email import EmailCapability
from overblick.capabilities.communication.gmail import GmailCapability
from overblick.capabilities.communication.telegram_notifier import TelegramNotifier
from overblick.capabilities.monitoring.inspector import HostInspectionCapability

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
    "stt": SpeechToTextCapability,
    "tts": TextToSpeechCapability,
    "vision": VisionCapability,
    "boss_request": BossRequestCapability,
    "email": EmailCapability,
    "gmail": GmailCapability,
    "telegram_notifier": TelegramNotifier,
    "host_inspection": HostInspectionCapability,
}

# Bundle -> capability names
# NOTE: "psychology" bundle is DEPRECATED as of v1.1. Use psychological_framework in personality.yaml instead.
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    "psychology": ["dream_system", "therapy_system", "emotional_state"],  # DEPRECATED
    "knowledge": ["safe_learning", "knowledge_loader"],
    "social": ["openings"],
    "engagement": ["analyzer", "composer"],
    "conversation": ["conversation_tracker"],
    "content": ["summarizer"],
    "speech": ["stt", "tts"],
    "vision": ["vision"],
    "communication": ["boss_request", "email", "gmail", "telegram_notifier"],
    "monitoring": ["host_inspection"],
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
    "BossRequestCapability",
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
    "SpeechToTextCapability",
    "TextToSpeechCapability",
    "VisionCapability",
    "EmailCapability",
    "GmailCapability",
    "TelegramNotifier",
    "HostInspectionCapability",
    "CAPABILITY_REGISTRY",
    "CAPABILITY_BUNDLES",
    "resolve_capabilities",
]
