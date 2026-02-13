"""
Identity loader — YAML config to frozen dataclass.

Each identity (Anomal, Cherry, ...) is defined by a set of YAML files
that control all behavioral differences: thresholds, toggles, prompts,
knowledge, quiet hours, LLM settings, and security deflections.
"""

import importlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)

# Base directory for identities within the package
_IDENTITIES_DIR = Path(__file__).parent.parent / "identities"


class LLMSettings(BaseModel):
    """LLM configuration for an identity."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    model: str = "qwen3:8b"
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2000
    timeout_seconds: int = 180

    # Gateway mode: use the LLM Gateway priority queue instead of direct Ollama
    use_gateway: bool = False
    gateway_url: str = "http://127.0.0.1:8200"


class QuietHoursSettings(BaseModel):
    """Quiet hours (bedroom mode) per identity."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    enabled: bool = True
    timezone: str = "Europe/Stockholm"
    start_hour: int = 21
    end_hour: int = 7
    mode: str = "sleep"


class ScheduleSettings(BaseModel):
    """Heartbeat and polling schedule."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    heartbeat_hours: int = 4
    feed_poll_minutes: int = 5
    enabled: bool = True


class SecuritySettings(BaseModel):
    """Security configuration."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    enable_preflight: bool = True
    enable_output_safety: bool = True
    admin_user_ids: tuple[str, ...] = ()
    block_threshold: int = 5
    block_duration_seconds: int = 1800


class Identity(BaseModel):
    """
    Immutable identity configuration.

    Loaded from YAML files in identities/<name>/ directory.
    Controls all behavioral differences between agent identities.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # Core identity
    name: str
    display_name: str = ""
    owner: str = ""
    version: str = "1.0.0"
    description: str = ""

    # Behavioral thresholds
    engagement_threshold: int = 35
    comment_cooldown_hours: int = 24

    # Enabled modules (conditional features, legacy)
    enabled_modules: tuple[str, ...] = ()

    # Connectors to load (new naming, falls back to plugin_names)
    connectors: tuple[str, ...] = ()

    # Capabilities to enable (new naming, falls back to enabled_modules)
    capability_names: tuple[str, ...] = ()

    # Sub-settings
    llm: LLMSettings = LLMSettings()
    quiet_hours: QuietHoursSettings = QuietHoursSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    security: SecuritySettings = SecuritySettings()

    # Personality reference (name of personality to load, defaults to identity name)
    personality_ref: str = ""

    # Loaded personality object (set during load_identity)
    loaded_personality: Any = None

    # Personality and knowledge (raw YAML data, legacy)
    personality: dict[str, Any] = {}
    opinions: dict[str, Any] = {}
    opsec: dict[str, Any] = {}
    knowledge: dict[str, Any] = {}

    # Deflection phrases (identity-specific, dict or list depending on YAML)
    deflections: dict[str, list[str]] | list[str] = {}

    # Interest keywords for engagement scoring
    interest_keywords: list[str] = []

    # Prompts module path (identity-specific)
    prompts_module: str = ""

    # Raw config dict (for plugin access to arbitrary keys)
    raw_config: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _set_display_name_default(cls, data):
        if isinstance(data, dict) and not data.get("display_name"):
            data["display_name"] = data.get("name", "").capitalize()
        return data

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


def load_identity(name: str) -> Identity:
    """
    Load an identity from its YAML configuration files.

    Looks for files in overblick/identities/<name>/:
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
    llm = LLMSettings.model_validate(llm_data) if llm_data else LLMSettings()

    qh_data = config.pop("quiet_hours", {})
    quiet_hours = QuietHoursSettings.model_validate(qh_data) if qh_data else QuietHoursSettings()

    sched_data = config.pop("schedule", {})
    schedule = ScheduleSettings.model_validate(sched_data) if sched_data else ScheduleSettings()

    sec_data = config.pop("security", {})
    security = SecuritySettings.model_validate(sec_data) if sec_data else SecuritySettings()

    # Pydantic auto-coerces list to tuple for tuple[str, ...] fields
    enabled_modules = config.pop("enabled_modules", [])
    connectors = config.pop("connectors", [])
    capability_names = config.pop("capabilities", [])

    deflections = config.pop("deflections", {})
    interest_keywords = config.pop("interest_keywords", [])

    # Rebuild raw_config from original YAML (before pops)
    raw_config = _load_yaml(identity_yaml)

    # Load personality (structured personality data)
    personality_ref = config.get("personality", name)
    loaded_personality = None
    try:
        from overblick.personalities import load_personality
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
        connectors=connectors,
        capability_names=capability_names,
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
        prompts_module=config.get("prompts_module", f"overblick.identities.{name}.prompts"),
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
