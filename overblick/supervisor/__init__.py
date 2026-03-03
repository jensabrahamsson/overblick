"""
Supervisor — multi-process agent lifecycle manager.

The Supervisor (aka Boss Agent) manages multiple agent processes,
handles inter-process communication, and arbitrates permission requests.
"""

from overblick.supervisor.ipc import IPCClient, IPCMessage, IPCServer
from overblick.supervisor.process import AgentProcess, ProcessState
from overblick.supervisor.supervisor import Supervisor, SupervisorState

__all__ = [
    "AgentProcess",
    "IPCClient",
    "IPCMessage",
    "IPCServer",
    "ProcessState",
    "Supervisor",
    "SupervisorState",
]
