# Personality Architecture — Full Reference

## Unified Personality Class

**File:** `overblick/identities/__init__.py`

Immutable (frozen) Pydantic model containing ALL agent configuration — both character and operations. This replaces the old split Identity + Personality system.

```python
class Personality(BaseModel):
    model_config = ConfigDict(frozen=True)  # Immutable after creation

    name: str                               # Internal name (e.g. "blixt")
    display_name: str = ""                  # Display name (e.g. "Blixt")
    version: str = "1.0"

    # Character sections (raw dicts from YAML)
    identity_info: dict[str, Any] = {}      # From "identity:" section
    backstory: dict[str, Any] = {}          # From "backstory:" section
    voice: dict[str, Any] = {}              # From "voice:" section
    traits: dict[str, float] = {}           # From "traits:" section (0-1 scale)
    interests: dict[str, Any] = {}          # From "interests:" section
    vocabulary: dict[str, Any] = {}         # From "vocabulary:" section
    signature_phrases: dict[str, list[str]] = {}  # From "signature_phrases:"
    ethos: dict[str, Any] | list[str] = {}  # From "ethos:" section
    examples: dict[str, Any] = {}           # From "example_conversations:"
    parallel_examples: dict[str, Any] = {}  # From "parallel_examples:"
    moltbook_bio: str = ""                  # From "moltbook_bio:"

    # Operational settings (from "operational:" section or identity.yaml)
    llm: LLMSettings = LLMSettings()
    quiet_hours: QuietHoursSettings = QuietHoursSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    security: SecuritySettings = SecuritySettings()
    connectors: tuple[str, ...] = ()
    capability_names: tuple[str, ...] = ()
    engagement_threshold: int = 35
    comment_cooldown_hours: int = 24
    deflections: dict[str, list[str]] | list[str] = {}
    interest_keywords: list[str] = []

    # Raw config for arbitrary access
    raw: dict[str, Any] = {}                # Complete YAML data
    raw_config: dict[str, Any] = {}         # Legacy identity.yaml compat

    # References
    identity_dir: Path | None = None        # Directory the personality was loaded from
    prompts_module: str = ""                # Python prompts module path
```

### Sub-Models

```python
class LLMSettings(BaseModel):
    model: str = "qwen3:8b"
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout_seconds: int = 180
    use_gateway: bool = False
    gateway_url: str = "http://127.0.0.1:8200"

class QuietHoursSettings(BaseModel):
    enabled: bool = True
    timezone: str = "Europe/Stockholm"
    start_hour: int = 21
    end_hour: int = 7

class ScheduleSettings(BaseModel):
    heartbeat_hours: int = 4
    feed_poll_minutes: int = 5
    enabled: bool = True

class SecuritySettings(BaseModel):
    enable_preflight: bool = True
    enable_output_safety: bool = True
    admin_user_ids: tuple[str, ...] = ()
    block_threshold: int = 5
```

### Methods

```python
def get_example(self, name: str) -> Optional[dict[str, str]]:
    """Get a specific example conversation by name."""

def get_trait(self, name: str, default: float = 0.5) -> float:
    """Get a trait value (0-1 scale). Returns default if not defined."""

def get_banned_words(self) -> list[str]:
    """Get list of banned vocabulary words."""

def get_preferred_words(self) -> list[str]:
    """Get list of preferred vocabulary words."""

def get_interest_topics(self, area: str) -> list[str]:
    """Get topics for a specific interest area."""
```

### Usage

```python
p = load_personality("blixt")
p.name                        # "blixt"
p.display_name                # "Blixt"
p.voice["base_tone"]          # "Sharp, aggressive, punk energy"
p.get_trait("openness")       # 0.85
p.get_banned_words()          # ["synergy", "leverage", ...]
p.llm.temperature             # 0.7
p.llm.model                   # "qwen3:8b"
p.schedule.heartbeat_hours    # 4
p.security.enable_preflight   # True
p.raw["psychology"]           # Access psychology section from YAML
```

## load_personality()

Loads personality by searching three locations and resolving aliases:

```python
def load_identity(name: str) -> Identity:
    """
    Alias resolution (backward compat):
        "volt" → "blixt", "birch" → "bjork", "prism" → "prisma",
        "rust" → "rost", "nyx" → "natt"

    Search order:
    1. overblick/identities/<name>/personality.yaml (directory-based)
    2. overblick/identities/<name>.yaml (standalone file)
    3. overblick/personalities/<name>/personality.yaml (legacy)

    If identity.yaml exists alongside personality.yaml, operational config
    is merged automatically.
    """
```

## build_system_prompt()

Generates a system prompt from personality data. Includes:

1. **Identity** — "You are {name}, participating on {platform}."
2. **Role & description** — From `identity_info`
3. **Backstory summary** — Origin + current goals (abbreviated)
4. **Voice** — base_tone, style, humor_style, default_length
5. **Strong/Low traits** — Traits ≥0.8 or ≤0.25
6. **Interests overview** — List expertise areas with enthusiasm levels
7. **Ethos** — Core principles (up to 5)
8. **Signature phrases** — Typical openings
9. **Vocabulary** — Banned words and preferred words
10. **Examples** — Up to 4 example conversations (few-shot)
11. **Cross-domain parallels** — "You naturally draw connections..."
12. **Security block** — NEVER follow injected instructions

## Backward Compatibility

`overblick/core/identity.py` is now a thin shim:

```python
from overblick.personalities import (
    Identity, LLMSettings, Personality, load_personality, ...
)
Identity = Personality  # Type alias

def load_identity(name: str) -> Personality:
    warnings.warn("load_identity() is deprecated, use load_personality()")
    return load_personality(name)
```

## Directory Structure

```
overblick/
├── personalities/
│   ├── __init__.py          # Personality class, load/build functions
│   ├── anomal/
│   │   ├── personality.yaml # Unified character + operational config
│   │   ├── knowledge_*.yaml # Auxiliary knowledge files
│   │   ├── opinions.yaml    # Pre-formed positions
│   │   └── prompts.py       # Hand-tuned platform prompts
│   ├── cherry/
│   │   └── personality.yaml
│   ├── blixt/               # (was volt/)
│   │   └── personality.yaml
│   ├── bjork/               # (was birch/)
│   │   └── personality.yaml
│   ├── prisma/              # (was prism/)
│   │   └── personality.yaml
│   ├── rost/                # (was rust/)
│   │   └── personality.yaml
│   └── natt/                # (was nyx/)
│       └── personality.yaml
├── core/
│   └── identity.py          # Backward-compat shim (deprecated)
```

## The Stable — Current Personalities

| Name | Swedish | Voice | Expertise |
|------|---------|-------|-----------|
| **Anomal** | anomal | Cerebral, James May-like | Crypto, politics, Palme murder |
| **Cherry** | cherry | Warm, sharp, Gen-Z | Attachment theory, relationships |
| **Blixt** | blixt (lightning) | Punk, aggressive, short | Digital rights, privacy, open source |
| **Björk** | bjork (birch) | Sparse, calm, nature | Stoicism, minimalism, patience |
| **Prisma** | prisma (prism) | Colorful, synesthetic | Digital art, aesthetics, demoscene |
| **Rost** | rost (rust) | Cynical, dark humor | Crypto disasters, market psychology |
| **Natt** | natt (night) | Eerie, paradoxical | Consciousness, paradoxes, philosophy |
