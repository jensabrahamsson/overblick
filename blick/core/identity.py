"""
Identity loader — YAML config to frozen dataclass.

Each identity (Anomal, Cherry, ...) is defined by a set of YAML files
that control all behavioral differences: thresholds, toggles, prompts,
knowledge, quiet hours, LLM settings, and security deflections.
"""

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Base directory for identities within the package
_IDENTITIES_DIR = Path(__file__).parent.parent / "identities"


@dataclass(frozen=True)
class LLMSettings:
    """LLM configuration for an identity."""
    model: str = "qwen3:8b"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2000
    timeout_seconds: int = 180


@dataclass(frozen=True)
class QuietHoursSettings:
    """Quiet hours (bedroom mode) per identity."""
    enabled: bool = True
    timezone: str = "Europe/Stockholm"
    start_hour: int = 21
    end_hour: int = 7
    mode: str = "sleep"


@dataclass(frozen=True)
class ScheduleSettings:
    """Heartbeat and polling schedule."""
    heartbeat_hours: int = 4
    feed_poll_minutes: int = 5
    enabled: bool = True


@dataclass(frozen=True)
class SecuritySettings:
    """Security configuration."""
    enable_preflight: bool = True
    enable_output_safety: bool = True
    admin_user_ids: tuple[str, ...] = ()
    block_threshold: int = 5
    block_duration_seconds: int = 1800


@dataclass(frozen=True)
class Identity:
    """
    Immutable identity configuration.

    Loaded from YAML files in identities/<name>/ directory.
    Controls all behavioral differences between agent identities.
    """
    # Core identity
    name: str
    display_name: str = ""
    owner: str = ""
    version: str = "1.0.0"
    description: str = ""

    # Behavioral thresholds
    engagement_threshold: int = 35
    comment_cooldown_hours: int = 24

    # Enabled modules (conditional features)
    enabled_modules: tuple[str, ...] = ()

    # Sub-settings
    llm: LLMSettings = field(default_factory=LLMSettings)
    quiet_hours: QuietHoursSettings = field(default_factory=QuietHoursSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)

    # Personality reference (name of personality to load, defaults to identity name)
    personality_ref: str = ""

    # Loaded personality object (set during load_identity)
    loaded_personality: Any = None

    # Personality and knowledge (raw YAML data, legacy)
    personality: dict[str, Any] = field(default_factory=dict)
    opinions: dict[str, Any] = field(default_factory=dict)
    opsec: dict[str, Any] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)

    # Deflection phrases (identity-specific)
    deflections: dict[str, list[str]] = field(default_factory=dict)

    # Interest keywords for engagement scoring
    interest_keywords: list[str] = field(default_factory=list)

    # Prompts module path (identity-specific)
    prompts_module: str = ""

    # Raw config dict (for plugin access to arbitrary keys)
    raw_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name.capitalize())

    def get_prompts_module(self) -> ModuleType:
        """Import and return the identity-specific prompts module."""
        if not self.prompts_module:
            raise ValueError(f"No prompts_module configured for identity '{self.name}'")
        return importlib.import_module(self.prompts_module)

    def has_module(self, module_name: str) -> bool:
        """Check if a module is enabled for this identity."""
        return module_name in self.enabled_modules

    @property
    def identity_dir(self) -> Path:
        """Path to this identity's directory."""
        return _IDENTITIES_DIR / self.name


def _load_yaml(path: Path) -> dict:
    """Load YAML file, returning empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _build_frozen(cls, data: dict):
    """Build a frozen dataclass from dict, ignoring unknown keys."""
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def load_identity(name: str) -> Identity:
    """
    Load an identity from its YAML configuration files.

    Looks for files in blick/identities/<name>/:
    - identity.yaml (required): Core settings, thresholds, toggles
    - personality.yaml: Voice, style, tone
    - opinions.yaml: Topic opinions
    - opsec.yaml: Operational security rules
    - knowledge_*.yaml: Domain knowledge files

    Args:
        name: Identity name (directory name under identities/)

    Returns:
        Frozen Identity dataclass

    Raises:
        FileNotFoundError: If identity directory or identity.yaml not found
    """
    identity_dir = _IDENTITIES_DIR / name

    if not identity_dir.exists():
        raise FileNotFoundError(f"Identity directory not found: {identity_dir}")

    # Load core identity config (required)
    identity_yaml = identity_dir / "identity.yaml"
    if not identity_yaml.exists():
        raise FileNotFoundError(f"identity.yaml not found for '{name}': {identity_yaml}")

    config = _load_yaml(identity_yaml)

    # Load optional YAML files
    personality = _load_yaml(identity_dir / "personality.yaml")
    opinions = _load_yaml(identity_dir / "opinions.yaml")
    opsec = _load_yaml(identity_dir / "opsec.yaml")

    # Load all knowledge files (knowledge_*.yaml)
    knowledge = {}
    for kf in sorted(identity_dir.glob("knowledge_*.yaml")):
        key = kf.stem.replace("knowledge_", "")
        knowledge[key] = _load_yaml(kf)

    # Build sub-settings
    llm_data = config.pop("llm", {})
    llm = _build_frozen(LLMSettings, llm_data) if llm_data else LLMSettings()

    qh_data = config.pop("quiet_hours", {})
    quiet_hours = _build_frozen(QuietHoursSettings, qh_data) if qh_data else QuietHoursSettings()

    sched_data = config.pop("schedule", {})
    schedule = _build_frozen(ScheduleSettings, sched_data) if sched_data else ScheduleSettings()

    sec_data = config.pop("security", {})
    if "admin_user_ids" in sec_data and isinstance(sec_data["admin_user_ids"], list):
        sec_data["admin_user_ids"] = tuple(sec_data["admin_user_ids"])
    security = _build_frozen(SecuritySettings, sec_data) if sec_data else SecuritySettings()

    # Convert lists to tuples for frozen dataclass
    enabled_modules = config.pop("enabled_modules", [])
    if isinstance(enabled_modules, list):
        enabled_modules = tuple(enabled_modules)

    deflections = config.pop("deflections", {})
    interest_keywords = config.pop("interest_keywords", [])

    # Rebuild raw_config from original YAML (before pops)
    raw_config = _load_yaml(identity_yaml)

    # Load personality (structured personality data)
    personality_ref = config.get("personality", name)
    loaded_personality = None
    try:
        from blick.personalities import load_personality
        loaded_personality = load_personality(personality_ref)
        logger.info("Loaded personality '%s' for identity '%s'", personality_ref, name)
    except FileNotFoundError:
        logger.debug("No personality found for '%s', continuing without", personality_ref)
    except Exception as e:
        logger.warning("Error loading personality '%s': %s", personality_ref, e)

    return Identity(
        name=name,
        display_name=config.get("display_name", name.capitalize()),
        owner=config.get("owner", ""),
        version=config.get("version", "1.0.0"),
        description=config.get("description", ""),
        engagement_threshold=config.get("engagement_threshold", 35),
        comment_cooldown_hours=config.get("comment_cooldown_hours", 24),
        enabled_modules=enabled_modules,
        llm=llm,
        quiet_hours=quiet_hours,
        schedule=schedule,
        security=security,
        personality_ref=personality_ref,
        loaded_personality=loaded_personality,
        personality=personality,
        opinions=opinions,
        opsec=opsec,
        knowledge=knowledge,
        deflections=deflections,
        interest_keywords=interest_keywords,
        prompts_module=config.get("prompts_module", f"blick.identities.{name}.prompts"),
        raw_config=raw_config,
    )


def list_identities() -> list[str]:
    """List available identity names."""
    if not _IDENTITIES_DIR.exists():
        return []
    return sorted(
        d.name
        for d in _IDENTITIES_DIR.iterdir()
        if d.is_dir() and (d / "identity.yaml").exists()
    )
