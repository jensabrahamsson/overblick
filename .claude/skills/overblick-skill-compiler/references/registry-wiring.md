# Registry Wiring

Exact patterns for updating the plugin and capability registries. These must be followed precisely — incorrect wiring means the component won't load.

## Plugin Registry

**File:** `overblick/core/plugin_registry.py`

The `_KNOWN_PLUGINS` dict maps plugin names to `(module_path, class_name)` tuples.

### Current Structure (line 17-33)

```python
_KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "gmail": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "discord": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "rss": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "webhook": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
    # Connector aliases (same classes, new names)
    "moltbook_connector": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "telegram_connector": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "gmail_connector": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "discord_connector": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "matrix_connector": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "rss_connector": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "webhook_connector": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
}
```

### How to Add a New Plugin

1. Add the main entry in **alphabetical order** among the primary entries (before the `# Connector aliases` comment):
   ```python
   "<name>": ("overblick.plugins.<name>.plugin", "<Name>Plugin"),
   ```

2. Add the connector alias after the `# Connector aliases` comment:
   ```python
   "<name>_connector": ("overblick.plugins.<name>.plugin", "<Name>Plugin"),
   ```

### Example: Adding "slack" Plugin

```python
_KNOWN_PLUGINS: dict[str, tuple[str, str]] = {
    "discord": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "gmail": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "matrix": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "moltbook": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "rss": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "slack": ("overblick.plugins.slack.plugin", "SlackPlugin"),        # NEW
    "telegram": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "webhook": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
    # Connector aliases (same classes, new names)
    "discord_connector": ("overblick.plugins.discord.plugin", "DiscordPlugin"),
    "gmail_connector": ("overblick.plugins.gmail.plugin", "GmailPlugin"),
    "matrix_connector": ("overblick.plugins.matrix.plugin", "MatrixPlugin"),
    "moltbook_connector": ("overblick.plugins.moltbook.plugin", "MoltbookPlugin"),
    "rss_connector": ("overblick.plugins.rss.plugin", "RSSPlugin"),
    "slack_connector": ("overblick.plugins.slack.plugin", "SlackPlugin"),  # NEW
    "telegram_connector": ("overblick.plugins.telegram.plugin", "TelegramPlugin"),
    "webhook_connector": ("overblick.plugins.webhook.plugin", "WebhookPlugin"),
}
```

**CRITICAL:** Plugins NOT in `_KNOWN_PLUGINS` cannot be loaded. This is a security whitelist.

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
}

CAPABILITY_BUNDLES: dict[str, list[str]] = {
    "psychology": ["dream_system", "therapy_system", "emotional_state"],
    "knowledge": ["safe_learning", "knowledge_loader"],
    "social": ["openings"],
    "engagement": ["analyzer", "composer"],
    "conversation": ["conversation_tracker"],
    "content": ["summarizer"],
    "speech": ["stt", "tts"],
    "vision": ["vision"],
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
