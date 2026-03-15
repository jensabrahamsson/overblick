from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from overblick.core.capability import CapabilityBase
from overblick.core.orchestrator_types import OrchestratorState
from overblick.core.plugin_base import PluginBase


@dataclass
class OrchestratorRuntimeState:
    lifecycle_state: OrchestratorState = OrchestratorState.INIT
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)

    plugins: list[PluginBase] = field(default_factory=list)
    capabilities: dict[str, CapabilityBase] = field(default_factory=dict)
    capabilities_setup: bool = False

    control_file: Path | None = None
    control_cache: dict[str, str] = field(default_factory=dict)
    control_cache_ts: float = 0.0
