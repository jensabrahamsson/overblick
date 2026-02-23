"""
Protocols for agentic plugin extension points.

Defines the minimal interfaces a plugin must implement to
participate in the agentic loop. Uses runtime-checkable protocols
for loose coupling — no base class inheritance required.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from overblick.core.agentic.models import ActionOutcome, PlannedAction


@runtime_checkable
class Observer(Protocol):
    """
    Collects world state for the agent.

    The returned observation is opaque to the core loop — only the
    plugin's observer and action handlers understand its structure.
    """

    async def observe(self) -> Any:
        """Collect and return the current world state."""
        ...

    def format_for_planner(self, observation: Any) -> str:
        """Format observation as human-readable text for the LLM planner."""
        ...


@runtime_checkable
class ActionHandler(Protocol):
    """
    Handles a single action type.

    Plugins register handlers by string key. The executor
    dispatches PlannedActions to the matching handler.
    """

    async def handle(
        self,
        action: PlannedAction,
        observation: Any,
    ) -> ActionOutcome:
        """
        Execute a planned action against the observed world state.

        Args:
            action: The planned action to execute
            observation: The current world state (domain-specific)

        Returns:
            ActionOutcome with success/failure and details
        """
        ...


class PlanningPromptConfig(BaseModel):
    """
    Configuration slots for the planning prompt template.

    Plugins provide domain-specific content for these slots,
    while the core builds the full prompt structure.
    """
    agent_role: str = "You are an autonomous agent."
    available_actions: str = ""  # Human-readable list of action types
    safety_rules: str = ""  # Domain-specific safety constraints
    output_format_hint: str = ""  # Extra format instructions
    learning_categories: str = ""  # Categories for reflection
