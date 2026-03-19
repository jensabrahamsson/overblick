from enum import Enum
from typing import NamedTuple


class OrchestratorState(Enum):
    INIT = "init"
    SETUP = "setup"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class LifecycleEvent(NamedTuple):
    identity_name: str
    state: OrchestratorState
    timestamp: float
