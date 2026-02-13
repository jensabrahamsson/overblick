"""
Supervisor — multi-process agent lifecycle manager.

The Supervisor (aka Boss Agent) manages multiple agent processes,
handles inter-process communication, and arbitrates permission requests.
"""

from blick.supervisor.supervisor import Supervisor, SupervisorState
from blick.supervisor.process import AgentProcess, ProcessState
from blick.supervisor.ipc import IPCServer, IPCClient, IPCMessage

__all__ = [
    "Supervisor",
    "SupervisorState",
    "AgentProcess",
    "ProcessState",
    "IPCServer",
    "IPCClient",
    "IPCMessage",
]
