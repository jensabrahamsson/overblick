"""
Unified models for the agentic core platform.

Provides domain-agnostic data structures for:
- Agent goals (persistent objectives)
- Agent learnings (extracted insights)
- Tick logs (cycle records)
- Planned actions and outcomes
- Action plans
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class GoalStatus(str, Enum):
    """Status of an agent goal."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AgentGoal(BaseModel):
    """A persistent goal the agent works toward."""
    id: Optional[int] = None
    name: str
    description: str
    priority: int = 50  # 0-100, higher = more important
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = 0.0  # 0.0-1.0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = {}


class PlannedAction(BaseModel):
    """A single action in the agent's plan (domain-agnostic)."""
    action_type: str  # string key — plugins register handlers by key
    target: str = ""  # e.g. "PR #42", "email from alice@"
    target_number: int = 0
    repo: str = ""  # optional — used by repo-based plugins
    priority: int = 50
    reasoning: str = ""
    params: dict[str, Any] = {}


class ActionPlan(BaseModel):
    """Ordered list of planned actions for a tick."""
    actions: list[PlannedAction] = []
    reasoning: str = ""  # Overall reasoning for the plan
    tick_summary: str = ""


class ActionOutcome(BaseModel):
    """Result of executing a planned action."""
    action: PlannedAction
    success: bool
    result: str = ""
    error: str = ""
    duration_ms: float = 0.0


class TickLog(BaseModel):
    """Record of a complete agent tick cycle."""
    id: Optional[int] = None
    tick_number: int = 0
    started_at: str = ""
    completed_at: str = ""
    observations_count: int = 0
    actions_planned: int = 0
    actions_executed: int = 0
    actions_succeeded: int = 0
    reasoning_summary: str = ""
    duration_ms: float = 0.0


class AgentLearning(BaseModel):
    """A learning/insight extracted from agent experience."""
    id: Optional[int] = None
    category: str = ""  # e.g. "dependabot", "email_classification"
    insight: str = ""
    confidence: float = 0.5
    source: str = "reflection"  # e.g. "reflection", "boss_feedback"
    source_tick: int = 0
    source_ref: Optional[str] = None  # optional reference (email_from, PR URL)
    created_at: str = ""
