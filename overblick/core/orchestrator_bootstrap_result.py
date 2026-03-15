from __future__ import annotations

from dataclasses import dataclass

from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_runtime_state import OrchestratorRuntimeState
from overblick.core.orchestrator_services import OrchestratorServices


@dataclass
class OrchestratorBootstrapResult:
    services: OrchestratorServices
    runtime_state: OrchestratorRuntimeState
    paths: OrchestratorPaths
