"""
Capabilities — composable behavioral building blocks for agent plugins.

Bundles:
    system         = [system_clock] — Core capabilities injected into all agents
    knowledge      = [loader]
    social         = [openings]
    engagement     = [analyzer, composer]
    conversation   = [conversation_tracker]
    content        = [summarizer]
    consulting     = [personality_consultant]
    speech         = [stt, tts]
    vision         = [vision]
    communication  = [boss_request, email, gmail, style_trainer, telegram_notifier]
    monitoring     = [host_inspection]
"""

from overblick.capabilities.communication.boss_request import BossRequestCapability
from overblick.capabilities.communication.email import EmailCapability
from overblick.capabilities.communication.gmail import GmailCapability
from overblick.capabilities.communication.style_trainer import StyleTrainerCapability
from overblick.capabilities.communication.telegram_notifier import TelegramNotifier
from overblick.capabilities.consulting.personality_consultant import PersonalityConsultantCapability
from overblick.capabilities.content.summarizer import SummarizerCapability
from overblick.capabilities.conversation.tracker import ConversationCapability
from overblick.capabilities.engagement.analyzer import AnalyzerCapability
from overblick.capabilities.engagement.composer import ComposerCapability
from overblick.capabilities.knowledge.loader import KnowledgeCapability
from overblick.capabilities.monitoring.inspector import HostInspectionCapability
from overblick.capabilities.psychology.dream import DreamCapability
from overblick.capabilities.psychology.emotional import EmotionalCapability
from overblick.capabilities.psychology.mood_cycle import MoodCycleCapability
from overblick.capabilities.psychology.therapy import TherapyCapability
from overblick.capabilities.social.openings import OpeningCapability
from overblick.capabilities.speech.stt import SpeechToTextCapability
from overblick.capabilities.speech.tts import TextToSpeechCapability
from overblick.capabilities.system.clock import SystemClockCapability
from overblick.capabilities.vision.analyzer import VisionCapability

# Name -> class mapping for registry
CAPABILITY_REGISTRY: dict[str, type] = {
    "dream_system": DreamCapability,
    "therapy_system": TherapyCapability,
    "emotional_state": EmotionalCapability,
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
    "style_trainer": StyleTrainerCapability,
    "telegram_notifier": TelegramNotifier,
    "host_inspection": HostInspectionCapability,
    "system_clock": SystemClockCapability,
    "personality_consultant": PersonalityConsultantCapability,
    "mood_cycle": MoodCycleCapability,
}

# Bundle -> capability names
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    "knowledge": ["knowledge_loader"],
    "social": ["openings"],
    "engagement": ["analyzer", "composer"],
    "conversation": ["conversation_tracker"],
    "content": ["summarizer"],
    "speech": ["stt", "tts"],
    "vision": ["vision"],
    "communication": ["boss_request", "email", "gmail", "style_trainer", "telegram_notifier"],
    "consulting": ["personality_consultant"],
    "monitoring": ["host_inspection"],
    "system": ["system_clock"],
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
    "CAPABILITY_BUNDLES",
    "CAPABILITY_REGISTRY",
    "AnalyzerCapability",
    "BossRequestCapability",
    "ComposerCapability",
    "ConversationCapability",
    "DreamCapability",
    "EmailCapability",
    "EmotionalCapability",
    "GmailCapability",
    "HostInspectionCapability",
    "KnowledgeCapability",
    "MoodCycleCapability",
    "OpeningCapability",
    "PersonalityConsultantCapability",
    "SpeechToTextCapability",
    "StyleTrainerCapability",
    "SummarizerCapability",
    "SystemClockCapability",
    "TelegramNotifier",
    "TextToSpeechCapability",
    "TherapyCapability",
    "VisionCapability",
    "resolve_capabilities",
]
