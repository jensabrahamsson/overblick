"""
AgentProcess — subprocess wrapper for managed agent identities.

Each agent identity runs in its own subprocess, managed by the Supervisor.
AgentProcess tracks process state, PID, and provides lifecycle methods.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessState(Enum):
    """Agent process lifecycle states."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass
class AgentProcess:
    """
    Wrapper around a subprocess running a Blick agent.

    Manages the lifecycle of a single agent identity running
    in its own process via `python -m blick run <identity>`.
    """

    identity: str
    plugins: list[str] = field(default_factory=lambda: ["moltbook"])
    state: ProcessState = ProcessState.PENDING
    pid: Optional[int] = None
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    restart_count: int = 0
    max_restarts: int = 3
    _process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)

    async def start(self) -> bool:
        """Start the agent subprocess."""
        if self.state == ProcessState.RUNNING:
            logger.warning("Agent '%s' already running (pid=%s)", self.identity, self.pid)
            return False

        self.state = ProcessState.STARTING
        logger.info("Starting agent '%s'...", self.identity)

        try:
            cmd = [
                sys.executable, "-m", "blick", "run", self.identity,
                "--plugins", ",".join(self.plugins),
            ]

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self.pid = self._process.pid
            self.state = ProcessState.RUNNING
            self.started_at = time.time()

            logger.info("Agent '%s' started (pid=%d)", self.identity, self.pid)
            return True

        except Exception as e:
            logger.error("Failed to start agent '%s': %s", self.identity, e)
            self.state = ProcessState.CRASHED
            return False

    async def stop(self, timeout: float = 10.0) -> bool:
        """Gracefully stop the agent subprocess."""
        if self.state != ProcessState.RUNNING or not self._process:
            return False

        self.state = ProcessState.STOPPING
        logger.info("Stopping agent '%s' (pid=%s)...", self.identity, self.pid)

        try:
            # Send SIGTERM for graceful shutdown
            self._process.send_signal(signal.SIGTERM)

            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # Force kill if graceful shutdown failed
                logger.warning("Agent '%s' did not stop gracefully, killing", self.identity)
                self._process.kill()
                await self._process.wait()

            self.state = ProcessState.STOPPED
            self.stopped_at = time.time()
            logger.info("Agent '%s' stopped", self.identity)
            return True

        except Exception as e:
            logger.error("Error stopping agent '%s': %s", self.identity, e)
            self.state = ProcessState.CRASHED
            return False

    async def monitor(self) -> Optional[int]:
        """
        Wait for process to exit. Returns exit code.

        Should be called as a background task to detect crashes.
        """
        if not self._process:
            return None

        returncode = await self._process.wait()

        if self.state == ProcessState.STOPPING:
            self.state = ProcessState.STOPPED
        elif returncode != 0:
            self.state = ProcessState.CRASHED
            logger.warning(
                "Agent '%s' crashed (exit=%d, restarts=%d/%d)",
                self.identity, returncode, self.restart_count, self.max_restarts,
            )

        self.stopped_at = time.time()
        return returncode

    @property
    def uptime_seconds(self) -> float:
        """Seconds since process started (0 if not running)."""
        if not self.started_at:
            return 0.0
        end = self.stopped_at or time.time()
        return end - self.started_at

    @property
    def is_alive(self) -> bool:
        """Check if process is still running."""
        if not self._process:
            return False
        return self._process.returncode is None

    def to_dict(self) -> dict:
        """Serialize to dict for IPC/API responses."""
        return {
            "identity": self.identity,
            "plugins": self.plugins,
            "state": self.state.value,
            "pid": self.pid,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "restart_count": self.restart_count,
        }
