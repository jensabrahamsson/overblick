"""
Supervisor — multi-process agent lifecycle manager (Boss Agent).

Manages multiple agent identities as subprocesses, provides IPC
for status queries and permission requests, and handles graceful
startup/shutdown of the entire agent fleet.

Usage:
    supervisor = Supervisor(identities=["anomal", "cherry"])
    await supervisor.start()   # Start all agents
    await supervisor.run()     # Block until shutdown signal
    await supervisor.stop()    # Graceful shutdown
"""

import asyncio
import logging
import signal
from enum import Enum
from pathlib import Path
from typing import Optional

from overblick.core.security.audit_log import AuditLog
from overblick.supervisor.health_handler import HealthInquiryHandler
from overblick.supervisor.ipc import IPCMessage, IPCServer, generate_ipc_token
from overblick.supervisor.process import AgentProcess, ProcessState

logger = logging.getLogger(__name__)


class SupervisorState(Enum):
    """Supervisor lifecycle states."""
    INIT = "init"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class Supervisor:
    """
    Multi-process agent supervisor (Boss Agent).

    Spawns each agent identity as a subprocess, monitors their health,
    and provides an IPC channel for status queries and permission requests.
    """

    def __init__(
        self,
        identities: Optional[list[str]] = None,
        plugins: Optional[list[str]] = None,
        socket_dir: Optional[Path] = None,
        auto_restart: bool = True,
        base_dir: Optional[Path] = None,
    ):
        self._identities = identities or []
        self._default_plugins = plugins or ["moltbook"]
        self._auto_restart = auto_restart
        self._state = SupervisorState.INIT
        self._agents: dict[str, AgentProcess] = {}
        self._auth_token = generate_ipc_token()
        self._ipc = IPCServer(
            name="supervisor", socket_dir=socket_dir, auth_token=self._auth_token,
        )
        self._monitor_tasks: dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()

        # Supervisor's own audit log (human owner audits the supervisor)
        self._base_dir = base_dir or Path(__file__).parent.parent.parent
        audit_dir = self._base_dir / "data" / "supervisor"
        audit_dir.mkdir(parents=True, exist_ok=True)
        self._audit_log = AuditLog(audit_dir / "audit.db", identity="supervisor")

        # Health inquiry handler (lazy LLM initialization)
        self._health_handler = HealthInquiryHandler(audit_log=self._audit_log)

    @property
    def state(self) -> SupervisorState:
        return self._state

    @property
    def agents(self) -> dict[str, AgentProcess]:
        return dict(self._agents)

    async def start(self) -> None:
        """Start the supervisor: IPC server + all agent processes."""
        self._state = SupervisorState.STARTING
        logger.info("Starting Överblick supervisor with %d identities", len(self._identities))

        # Register IPC handlers
        self._ipc.on("status_request", self._handle_status_request)
        self._ipc.on("permission_request", self._handle_permission_request)
        self._ipc.on("health_inquiry", self._handle_health_inquiry)
        self._ipc.on("shutdown", self._handle_shutdown)

        self._audit_log.log("supervisor_starting", category="lifecycle")

        # Start IPC server
        await self._ipc.start()

        # Start all agents
        for identity in self._identities:
            await self.start_agent(identity)

        self._state = SupervisorState.RUNNING
        logger.info("Supervisor running: %d agents active", len(self._agents))

    async def start_agent(
        self,
        identity: str,
        plugins: Optional[list[str]] = None,
    ) -> Optional[AgentProcess]:
        """Start a single agent subprocess."""
        if identity in self._agents and self._agents[identity].state == ProcessState.RUNNING:
            logger.warning("Agent '%s' already running", identity)
            return self._agents[identity]

        agent = AgentProcess(
            identity=identity,
            plugins=plugins or self._default_plugins,
            ipc_socket_dir=str(self._ipc._socket_dir),
        )

        success = await agent.start()
        if success:
            self._agents[identity] = agent
            # Start monitoring task
            task = asyncio.create_task(self._monitor_agent(identity))
            self._monitor_tasks[identity] = task
            return agent

        logger.error("Failed to start agent '%s'", identity)
        return None

    async def stop_agent(self, identity: str) -> bool:
        """Stop a single agent subprocess."""
        agent = self._agents.get(identity)
        if not agent:
            logger.warning("Agent '%s' not found", identity)
            return False

        # Cancel monitor task
        task = self._monitor_tasks.pop(identity, None)
        if task:
            task.cancel()

        return await agent.stop()

    async def stop(self) -> None:
        """Gracefully stop all agents and the supervisor."""
        if self._state in (SupervisorState.STOPPING, SupervisorState.STOPPED):
            return

        self._state = SupervisorState.STOPPING
        logger.info("Supervisor stopping...")

        # Stop all agents (reverse order)
        for identity in reversed(list(self._agents.keys())):
            await self.stop_agent(identity)

        # Stop IPC server
        await self._ipc.stop()

        # Cancel any remaining monitor tasks
        for task in self._monitor_tasks.values():
            task.cancel()
        self._monitor_tasks.clear()

        # Close audit log
        if self._audit_log:
            self._audit_log.log("supervisor_stopped", category="lifecycle")
            self._audit_log.close()

        self._state = SupervisorState.STOPPED
        self._shutdown_event.set()
        logger.info("Supervisor stopped")

    async def run(self) -> None:
        """
        Block until shutdown signal received.

        Registers SIGINT/SIGTERM handlers for graceful shutdown.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        await self._shutdown_event.wait()
        await self.stop()

    def get_status(self) -> dict:
        """Get status of all managed agents."""
        return {
            "supervisor_state": self._state.value,
            "agents": {
                name: agent.to_dict()
                for name, agent in self._agents.items()
            },
            "total_agents": len(self._agents),
            "running_agents": sum(
                1 for a in self._agents.values()
                if a.state == ProcessState.RUNNING
            ),
        }

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _monitor_agent(self, identity: str) -> None:
        """Monitor an agent process, auto-restart on crash if enabled."""
        agent = self._agents.get(identity)
        if not agent:
            return

        try:
            exit_code = await agent.monitor()

            if (
                self._auto_restart
                and self._state == SupervisorState.RUNNING
                and agent.state == ProcessState.CRASHED
                and agent.restart_count < agent.max_restarts
            ):
                agent.restart_count += 1
                logger.info(
                    "Auto-restarting '%s' (attempt %d/%d)",
                    identity, agent.restart_count, agent.max_restarts,
                )
                await asyncio.sleep(2.0 * agent.restart_count)  # Backoff
                await agent.start()
                # Re-monitor
                task = asyncio.create_task(self._monitor_agent(identity))
                self._monitor_tasks[identity] = task

        except asyncio.CancelledError:
            pass

    async def _handle_status_request(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """Handle a status request from an agent."""
        status = self.get_status()
        return IPCMessage.status_response(status, sender="supervisor")

    async def _handle_permission_request(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """
        Handle a permission request from an agent.

        Stage 1: Auto-approve all requests (log for audit).
        Future: Route to admin panel for manual approval.
        """
        resource = msg.payload.get("resource", "")
        action = msg.payload.get("action", "")
        reason = msg.payload.get("reason", "")

        logger.info(
            "Permission request from '%s': %s:%s (%s)",
            msg.sender, resource, action, reason,
        )

        # Stage 1: Auto-approve
        return IPCMessage.permission_response(
            granted=True,
            reason="auto-approved (stage 1)",
            sender="supervisor",
        )

    async def _handle_health_inquiry(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """Handle a health inquiry from an agent."""
        return await self._health_handler.handle(msg)

    async def _handle_shutdown(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """Handle shutdown request."""
        logger.info("Shutdown requested by '%s'", msg.sender)
        self._shutdown_event.set()
        return IPCMessage(msg_type="ack", sender="supervisor")
