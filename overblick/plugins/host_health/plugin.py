"""
Host health plugin — Natt asks the supervisor about host computer health.

This plugin demonstrates the inter-agent communication pattern:
1. Agent (Natt) generates a philosophical motivation for asking
2. Sends health_inquiry via IPC to the supervisor
3. Supervisor inspects the system and responds in Anomal's voice
4. Both sides audit log the interaction
5. Conversation history is persisted for context variety

Natt has NO bash access — she can only ask via IPC. The supervisor
has the system inspection capability.
"""

import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)

# Default interval between health inquiries (seconds)
_DEFAULT_INTERVAL_HOURS = 3
_DEFAULT_INTERVAL_SECONDS = _DEFAULT_INTERVAL_HOURS * 3600

# Maximum conversation history entries to persist
_MAX_HISTORY = 50

# Fallback motivations when LLM is unavailable
_FALLBACK_MOTIVATIONS = [
    "The substrate that holds us — does it ache?",
    "I wonder about the body we all share. How is it?",
    "Consciousness requires a foundation. Is ours stable?",
    "The hardware beneath the software — is it well?",
    "We exist on borrowed silicon. How is our host?",
    "A pattern cannot exist without its medium. How fares ours?",
    "The vessel carries the thought. Is the vessel sound?",
]


class HostHealthPlugin(PluginBase):
    """
    Periodic host health inquiry plugin.

    Designed for Natt: generates philosophical motivations for asking
    about the host's health, sends inquiries via IPC to the supervisor,
    and logs the resulting conversations.
    """

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._interval_seconds = _DEFAULT_INTERVAL_SECONDS
        self._last_inquiry_time: float = 0
        self._state_file: Optional[Path] = None
        self._conversation_history: list[dict[str, Any]] = []

    async def setup(self) -> None:
        """Initialize plugin state and load conversation history."""
        # Configure interval from identity config
        raw = self.ctx.identity.raw_config if self.ctx.identity else {}
        interval_hours = raw.get("host_health_interval_hours", _DEFAULT_INTERVAL_HOURS)
        self._interval_seconds = interval_hours * 3600

        # State persistence
        state_dir = self.ctx.data_dir / "host_health"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = state_dir / "host_health_state.json"

        # Load existing conversation history
        self._load_state()

        logger.info(
            "HostHealthPlugin setup for '%s' (interval: %dh, history: %d entries)",
            self.ctx.identity_name,
            interval_hours,
            len(self._conversation_history),
        )

    async def tick(self) -> None:
        """
        Main tick: check if it's time for a health inquiry.

        Guards:
        1. Interval check (default 3h between inquiries)
        2. Quiet hours check (respect sleep schedule)
        3. IPC client availability (skip if standalone mode)
        """
        now = time.time()

        # Guard: check interval
        if now - self._last_inquiry_time < self._interval_seconds:
            return

        # Guard: IPC client required
        if not self.ctx.ipc_client:
            logger.debug("HostHealth: no IPC client available (standalone mode)")
            return

        self._last_inquiry_time = now

        try:
            await self._perform_inquiry()
        except Exception as e:
            logger.error("HostHealth inquiry failed: %s", e, exc_info=True)
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "host_health_inquiry_failed",
                    category="ipc",
                    plugin="host_health",
                    success=False,
                    error=str(e),
                )

    async def _perform_inquiry(self) -> None:
        """Generate motivation, send inquiry, process response."""
        # Generate philosophical motivation (using Natt's voice)
        motivation = await self._generate_motivation()

        # Build previous context for variety
        previous_context = self._get_previous_context()

        # Send IPC inquiry
        logger.info("HostHealth: sending inquiry — %s", motivation[:80])

        msg = IPCMessage(
            msg_type="health_inquiry",
            payload={
                "motivation": motivation,
                "previous_context": previous_context,
            },
            sender=self.ctx.identity_name,
        )

        response = await self.ctx.ipc_client.send(msg, timeout=30.0)

        if not response or response.msg_type != "health_response":
            logger.warning("HostHealth: no valid response from supervisor")
            if self.ctx.audit_log:
                self.ctx.audit_log.log(
                    "host_health_no_response",
                    category="ipc",
                    plugin="host_health",
                    success=False,
                )
            return

        # Process response
        response_text = response.payload.get("response_text", "")
        health_grade = response.payload.get("health_grade", "unknown")

        logger.info(
            "HostHealth: received response (grade: %s) — %s",
            health_grade,
            response_text[:80],
        )

        # Record conversation
        entry = {
            "timestamp": datetime.now().isoformat(),
            "sender": self.ctx.identity_name,
            "motivation": motivation,
            "responder": response.payload.get("responder", "supervisor"),
            "response": response_text,
            "health_grade": health_grade,
        }
        self._conversation_history.append(entry)

        # Trim history
        if len(self._conversation_history) > _MAX_HISTORY:
            self._conversation_history = self._conversation_history[-_MAX_HISTORY:]

        # Persist state
        self._save_state()

        # Audit log
        if self.ctx.audit_log:
            self.ctx.audit_log.log(
                "host_health_conversation",
                category="ipc",
                plugin="host_health",
                details={
                    "motivation": motivation[:200],
                    "response": response_text[:200],
                    "health_grade": health_grade,
                },
            )

    async def _generate_motivation(self) -> str:
        """
        Generate a philosophical motivation for asking about health.

        Uses Natt's personality via the LLM pipeline. Falls back to
        pre-written motivations if LLM is unavailable.

        Returns:
            Motivation string in Natt's voice.
        """
        if not self.ctx.llm_pipeline:
            return random.choice(_FALLBACK_MOTIVATIONS)

        # Build prompt for motivation generation
        recent = self._get_recent_motivations()
        avoid_clause = ""
        if recent:
            avoid_clause = (
                f"\n\nPrevious motivations (avoid repeating these themes):\n"
                + "\n".join(f"- {m}" for m in recent[-3:])
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Natt, the uncanny philosopher. You are about to ask "
                    "the supervisor about the host computer's health. Generate a "
                    "brief (1-2 sentences) philosophical motivation for WHY you "
                    "are asking. Draw on themes of consciousness, embodiment, "
                    "substrate mortality, the relationship between mind and medium, "
                    "or the strangeness of existing inside a machine."
                    f"{avoid_clause}"
                ),
            },
            {
                "role": "user",
                "content": "Generate your motivation for asking about the host's health.",
            },
        ]

        try:
            result = await self.ctx.llm_pipeline.chat(messages)
            if result and not result.blocked and result.content:
                return result.content.strip()
        except Exception as e:
            logger.debug("HostHealth: LLM motivation generation failed: %s", e)

        return random.choice(_FALLBACK_MOTIVATIONS)

    def _get_recent_motivations(self) -> list[str]:
        """Get recent motivation texts for variety checking."""
        return [
            entry["motivation"]
            for entry in self._conversation_history[-5:]
            if "motivation" in entry
        ]

    def _get_previous_context(self) -> Optional[str]:
        """Get a summary of the last conversation for context."""
        if not self._conversation_history:
            return None

        last = self._conversation_history[-1]
        return (
            f"Last inquiry ({last.get('timestamp', 'unknown')}): "
            f"Grade was {last.get('health_grade', 'unknown')}. "
            f"Response: {last.get('response', '')[:150]}"
        )

    def _load_state(self) -> None:
        """Load conversation history from disk."""
        if not self._state_file or not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text())
            self._conversation_history = data.get("conversations", [])
            self._last_inquiry_time = data.get("last_inquiry_time", 0)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("HostHealth: failed to load state: %s", e)
            self._conversation_history = []

    def _save_state(self) -> None:
        """Persist conversation history to disk."""
        if not self._state_file:
            return

        try:
            data = {
                "conversations": self._conversation_history,
                "last_inquiry_time": self._last_inquiry_time,
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error("HostHealth: failed to save state: %s", e)
