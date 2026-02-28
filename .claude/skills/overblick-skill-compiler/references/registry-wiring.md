# Registry Wiring

Exact patterns for updating the plugin and capability registries. These must be followed precisely — incorrect wiring means the component won't load.

## Plugin Registry

**File:** `overblick/core/plugin_registry.py`

The `_DEFAULT_PLUGINS` dict maps plugin names to `(module_path, class_name)` tuples. Each `PluginRegistry` instance gets its own copy of this dict in `__init__` to prevent cross-instance pollution during testing.

### Current Structure

```python
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    "ai_digest": ("overblick.plugins.ai_digest.plugin", "AiDigestPlugin"),
    "compass": ("overblick.plugins.compass.plugin", "CompassPlugin"),
    "dev_agent": ("overblick.plugins.dev_agent.plugin", "DevAgentPlugin"),
    "email_agent": ("overblick.plugins.email_agent.plugin", "EmailAgentPlugin"),
    "github": ("overblick.plugins.github.plugin", "GitHubAgentPlugin"),
    "host_health": ("overblick.plugins.host_health.plugin", "HostHealthPlugin"),
    "irc": ("overblick.plugins.irc.plugin", "IRCPlugin"),
    "kontrast": ("overblick.plugins.kontrast.plugin", "KontrastPlugin"),
    "log_agent": ("overblick.plugins.log_agent.plugin", "LogAgentPlugin"),
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "skuggspel": ("overblick.plugins.skuggspel.plugin", "SkuggspelPlugin"),
    "spegel": ("overblick.plugins.spegel.plugin", "SpegelPlugin"),
    "stage": ("overblick.plugins.stage.plugin", "StagePlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
}

# Module-level alias for backward compatibility (tests import this)
_KNOWN_PLUGINS = _DEFAULT_PLUGINS
```

**Note:** Agentic plugins (`dev_agent`, `github`, `log_agent`) extend `AgenticPluginBase` and follow the OBSERVE/THINK/PLAN/ACT/REFLECT loop.

**NOTE:** There are NO connector aliases. The old `<name>_connector` pattern was removed. Each plugin has exactly one entry.

### How to Add a New Plugin

1. Add the entry in **alphabetical order** in `_DEFAULT_PLUGINS`:
   ```python
   "<name>": ("overblick.plugins.<name>.plugin", "<Name>Plugin"),
   ```

That's it. No connector alias needed.

### Example: Adding "slack" Plugin

```python
_DEFAULT_PLUGINS: dict[str, tuple[str, str]] = {
    "ai_digest": ("overblick.plugins.ai_digest.plugin", "AiDigestPlugin"),
    "compass": ("overblick.plugins.compass.plugin", "CompassPlugin"),
    "dev_agent": ("overblick.plugins.dev_agent.plugin", "DevAgentPlugin"),
    "email_agent": ("overblick.plugins.email_agent.plugin", "EmailAgentPlugin"),
    "github": ("overblick.plugins.github.plugin", "GitHubAgentPlugin"),
    "host_health": ("overblick.plugins.host_health.plugin", "HostHealthPlugin"),
    "irc": ("overblick.plugins.irc.plugin", "IRCPlugin"),
    "kontrast": ("overblick.plugins.kontrast.plugin", "KontrastPlugin"),
    "log_agent": ("overblick.plugins.log_agent.plugin", "LogAgentPlugin"),
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "skuggspel": ("overblick.plugins.skuggspel.plugin", "SkuggspelPlugin"),
    "slack": ("overblick.plugins.slack.plugin", "SlackPlugin"),        # NEW
    "spegel": ("overblick.plugins.spegel.plugin", "SpegelPlugin"),
    "stage": ("overblick.plugins.stage.plugin", "StagePlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
}
```

**CRITICAL:** Plugins NOT in `_DEFAULT_PLUGINS` cannot be loaded. This is a security whitelist.

### How PluginRegistry Works

```python
class PluginRegistry:
    def __init__(self):
        self._loaded: dict[str, PluginBase] = {}
        self._plugins: dict[str, tuple[str, str]] = dict(_DEFAULT_PLUGINS)  # Per-instance copy

    def register(self, name, module_path, class_name):
        self._plugins[name] = (module_path, class_name)

    def load(self, name, ctx) -> PluginBase:
        # Validates name is in whitelist, imports module, instantiates class
```

## Capability Registry

**File:** `overblick/capabilities/__init__.py`

Four places to update:

### 1. Add Import

Add the import in the import block, grouped by bundle:

```python
from overblick.capabilities.<bundle>.<name> import <Name>Capability
```

### 2. Add to CAPABILITY_REGISTRY

Add to the `CAPABILITY_REGISTRY` dict (registry name -> class):

```python
CAPABILITY_REGISTRY: dict[str, type] = {
    # ... existing entries ...
    "<registry_name>": <Name>Capability,
}
```

The `registry_name` is typically the capability's `name` attribute (e.g., `"summarizer"`, `"dream_system"`).

### 3. Add to CAPABILITY_BUNDLES

Add to the appropriate bundle in `CAPABILITY_BUNDLES`:

```python
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    # ... existing bundles ...
    "<bundle>": ["existing_cap", "<registry_name>"],  # Add to existing bundle
    # OR
    "<new_bundle>": ["<registry_name>"],  # Create new bundle
}
```

### 4. Add to \_\_all\_\_

Add the class name to `__all__`:

```python
__all__ = [
    # ... existing entries ...
    "<Name>Capability",
    # ...
]
```

### Current Structure

```python
# Imports (grouped by bundle)
from overblick.capabilities.psychology.dream import DreamCapability
from overblick.capabilities.psychology.therapy import TherapyCapability
from overblick.capabilities.psychology.emotional import EmotionalCapability
from overblick.capabilities.psychology.mood_cycle import MoodCycleCapability
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
from overblick.capabilities.communication.style_trainer import StyleTrainerCapability
from overblick.capabilities.communication.telegram_notifier import TelegramNotifier
from overblick.capabilities.consulting.personality_consultant import PersonalityConsultantCapability
from overblick.capabilities.monitoring.inspector import HostInspectionCapability
from overblick.capabilities.system.clock import SystemClockCapability

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
    "style_trainer": StyleTrainerCapability,
    "telegram_notifier": TelegramNotifier,
    "host_inspection": HostInspectionCapability,
    "system_clock": SystemClockCapability,
    "personality_consultant": PersonalityConsultantCapability,
    "mood_cycle": MoodCycleCapability,
}

# NOTE: "psychology" bundle is DEPRECATED as of v1.1.
# Use psychological_framework in personality.yaml instead.
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    "psychology": ["dream_system", "therapy_system", "emotional_state", "mood_cycle"],  # DEPRECATED
    "knowledge": ["safe_learning", "knowledge_loader"],
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
```

### Example: Adding "sentiment" to the "engagement" Bundle

```python
# Add import
from overblick.capabilities.engagement.sentiment import SentimentCapability

# Add to registry
CAPABILITY_REGISTRY: dict[str, type] = {
    # ...
    "sentiment": SentimentCapability,
    # ...
}

# Add to bundle
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    # ...
    "engagement": ["analyzer", "composer", "sentiment"],
    # ...
}

# Add to __all__
__all__ = [
    # ...
    "SentimentCapability",
    # ...
]
```

### Example: Creating a New "analytics" Bundle

```python
# Add import
from overblick.capabilities.analytics.tracker import TrackerCapability

# Add to registry
CAPABILITY_REGISTRY: dict[str, type] = {
    # ...
    "analytics_tracker": TrackerCapability,
    # ...
}

# Add new bundle
CAPABILITY_BUNDLES: dict[str, list[str]] = {
    # ...
    "analytics": ["analytics_tracker"],
    # ...
}

# Add to __all__
__all__ = [
    # ...
    "TrackerCapability",
    # ...
]
```

Also create `overblick/capabilities/analytics/__init__.py`:
```python
from overblick.capabilities.analytics.tracker import TrackerCapability

__all__ = ["TrackerCapability"]
```
