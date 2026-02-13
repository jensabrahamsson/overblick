"""
Backward compatibility — Identity is now Personality.

The unified Personality model in overblick.personalities replaces the old
separate Identity + Personality system. This module re-exports everything
for backward compatibility.

Usage (preferred):
    from overblick.personalities import Personality, load_personality

Usage (legacy, still works):
    from overblick.core.identity import Identity, load_identity
"""

import warnings

from overblick.personalities import (
    Identity,
    LLMSettings,
    Personality,
    QuietHoursSettings,
    ScheduleSettings,
    SecuritySettings,
    _load_yaml,
    list_personalities,
    load_personality,
)


def load_identity(name: str) -> Personality:
    """Load a personality by name. Deprecated — use load_personality()."""
    warnings.warn(
        "load_identity() is deprecated, use load_personality()",
        DeprecationWarning,
        stacklevel=2,
    )
    return load_personality(name)


def list_identities() -> list[str]:
    """List available personalities. Deprecated — use list_personalities()."""
    warnings.warn(
        "list_identities() is deprecated, use list_personalities()",
        DeprecationWarning,
        stacklevel=2,
    )
    return list_personalities()


__all__ = [
    "Identity",
    "LLMSettings",
    "Personality",
    "QuietHoursSettings",
    "ScheduleSettings",
    "SecuritySettings",
    "load_identity",
    "load_personality",
    "list_identities",
    "list_personalities",
    "_load_yaml",
]
