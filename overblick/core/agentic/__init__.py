"""
Agentic core platform — reusable OBSERVE/THINK/PLAN/ACT/REFLECT infrastructure.

Any plugin that wants to be an "agent" inherits AgenticPluginBase and
implements a few domain-specific methods. The agentic loop, goal tracking,
learning extraction, action planning, execution, and reflection are all
provided by the platform.
"""

from overblick.core.agentic.database import AGENTIC_MIGRATIONS, AgenticDB
from overblick.core.agentic.executor import ActionExecutor
from overblick.core.agentic.goal_tracker import GoalTracker
from overblick.core.agentic.loop import AgentLoop
from overblick.core.agentic.models import (
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)
from overblick.core.agentic.planner import ActionPlanner
from overblick.core.agentic.plugin_base import AgenticPluginBase
from overblick.core.agentic.protocols import (
    ActionHandler,
    Observer,
    PlanningPromptConfig,
)
from overblick.core.agentic.reflection import ReflectionPipeline

__all__ = [
    # Components
    "AGENTIC_MIGRATIONS",
    "ActionExecutor",
    # Protocols
    "ActionHandler",
    # Models
    "ActionOutcome",
    "ActionPlan",
    "ActionPlanner",
    "AgentGoal",
    "AgentLearning",
    "AgentLoop",
    "AgenticDB",
    # Plugin base
    "AgenticPluginBase",
    "GoalStatus",
    "GoalTracker",
    "Observer",
    "PlannedAction",
    "PlanningPromptConfig",
    "ReflectionPipeline",
    "TickLog",
]
