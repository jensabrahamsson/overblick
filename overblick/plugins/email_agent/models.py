"""
Pydantic models for the email agent plugin.

Defines the data structures for email classification, agent state,
learnings from boss feedback, and goal tracking.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class EmailIntent(str, Enum):
    """Actions the email agent can take on an incoming email."""
    IGNORE = "ignore"
    NOTIFY = "notify"       # Send Telegram notification to Jens
    REPLY = "reply"         # Write reply as Jens's assistant
    ASK_BOSS = "ask_boss"   # Uncertain — ask supervisor for guidance


class EmailClassification(BaseModel):
    """Result of LLM classification of an incoming email."""
    intent: EmailIntent
    confidence: float       # 0.0 - 1.0
    reasoning: str          # Why this classification
    priority: str = "normal"  # "low", "normal", "high", "urgent"


class AgentGoal(BaseModel):
    """A goal tracked by the agent for self-directed behavior."""
    id: Optional[int] = None
    description: str
    priority: int = 50      # 0-100
    progress: float = 0.0   # 0.0 - 1.0
    status: str = "active"  # active, completed, paused


class AgentLearning(BaseModel):
    """A learning extracted from boss feedback or self-reflection."""
    id: Optional[int] = None
    learning_type: str      # "classification", "reply_style", "sender_pattern"
    content: str
    source: str             # "boss_feedback", "self_reflection", "explicit"
    email_from: Optional[str] = None


class EmailRecord(BaseModel):
    """Record of an email processed by the agent."""
    id: Optional[int] = None
    email_from: str
    email_subject: str
    email_snippet: str = ""
    classified_intent: str
    confidence: float
    reasoning: str
    action_taken: str = ""
    boss_feedback: Optional[str] = None
    was_correct: Optional[bool] = None


class SenderProfile(BaseModel):
    """
    Consolidated sender profile — GDPR-safe.

    Stored as a JSON file per sender. Contains only non-personal
    aggregate data: interaction counts, language preference, typical
    intent distribution. No email bodies, no personal data.
    """
    email: str
    display_name: str = ""
    total_interactions: int = 0
    preferred_language: str = ""
    intent_distribution: dict[str, int] = {}  # intent -> count
    avg_confidence: float = 0.0
    last_interaction_date: str = ""  # ISO date only, no time
    notes: str = ""  # Non-GDPR agent notes (e.g. "prefers formal tone")


class AgentState(BaseModel):
    """Current agent state — loaded from DB, held in memory."""
    goals: list[AgentGoal] = []
    emails_processed: int = 0
    emails_replied: int = 0
    notifications_sent: int = 0
    boss_consultations: int = 0
    confidence_threshold: float = 0.7  # Below this -> ask_boss
    last_check: Optional[float] = None
    current_health: str = "nominal"
