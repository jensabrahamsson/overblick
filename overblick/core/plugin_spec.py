from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PluginSpec:
    name: str
    source: str = "registry"
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
