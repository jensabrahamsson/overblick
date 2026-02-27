"""
Shared utilities for plugin detection and path resolution in dashboard routes.

Provides helpers to check if a specific plugin is configured for any identity,
reading from all three configuration sources (personality.yaml top-level,
personality.yaml operational section, and identity.yaml).
"""

import re
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Request

# Shared identity/plugin name validation regex (no hyphens — lowercase + digits + underscores)
IDENTITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def resolve_base_dir(request: Request) -> Path:
    """Get the project base directory from app config."""
    cfg = request.app.state.config
    if cfg.base_dir:
        return Path(cfg.base_dir)
    return Path(__file__).parent.parent.parent.parent


def resolve_data_root(request: Request) -> Path:
    """Get the data root directory from app config."""
    return resolve_base_dir(request) / "data"


def resolve_identities_dir(request: Optional[Request] = None) -> Path:
    """Get the identities directory, resolving from app config if available."""
    if request is not None:
        return resolve_base_dir(request) / "overblick" / "identities"
    return Path("overblick/identities")


def is_plugin_configured(plugin_name: str) -> bool:
    """Return True if any identity has the given plugin configured."""
    identities_dir = resolve_identities_dir()
    if not identities_dir.exists():
        return False
    for d in identities_dir.iterdir():
        if not d.is_dir():
            continue
        if plugin_name in collect_plugins(d):
            return True
    return False


def collect_plugins(identity_dir: Path) -> set[str]:
    """Collect plugins from all config sources for an identity.

    Plugins can be defined in three places:
    1. personality.yaml top-level ``plugins:``
    2. personality.yaml ``operational.plugins:``
    3. identity.yaml ``plugins:``
    """
    plugins: set[str] = set()

    data = safe_load_yaml(identity_dir / "personality.yaml")
    top = data.get("plugins", [])
    if isinstance(top, list):
        plugins.update(top)
    op = data.get("operational", {})
    if isinstance(op, dict):
        op_plugins = op.get("plugins", [])
        if isinstance(op_plugins, list):
            plugins.update(op_plugins)

    id_data = safe_load_yaml(identity_dir / "identity.yaml")
    id_plugins = id_data.get("plugins", [])
    if isinstance(id_plugins, list):
        plugins.update(id_plugins)

    return plugins


def safe_load_yaml(path: Path) -> dict:
    """Load a YAML file safely, returning {} on any error."""
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}
