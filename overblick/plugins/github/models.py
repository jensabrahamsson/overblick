"""
Pydantic models for the GitHub agent plugin.

Defines data structures for:
- GitHub events and legacy bot-pattern models (kept for backward compat)
- Repository observations (world state snapshots)
- GitHub-specific action types

Generic agentic models (AgentGoal, AgentLearning, TickLog, PlannedAction,
ActionPlan, ActionOutcome, GoalStatus) are re-exported from core.
"""

from enum import Enum, StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field

# Re-export core agentic models for backward compatibility
from overblick.core.agentic.models import (
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)

# ---------------------------------------------------------------------------
# Legacy bot-pattern models (used by decision_engine.py, response_gen.py)
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    """Types of GitHub events the plugin tracks."""

    ISSUE_OPENED = "issue_opened"
    ISSUE_COMMENT = "issue_comment"
    MENTION = "mention"


class EventAction(StrEnum):
    """Actions the plugin can take on a GitHub event."""

    RESPOND = "respond"
    NOTIFY = "notify"
    SKIP = "skip"


class GitHubEvent(BaseModel):
    """A GitHub event (issue or comment) to evaluate."""

    event_id: str
    event_type: EventType
    repo: str
    issue_number: int
    issue_title: str = ""
    body: str = ""
    author: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: str = ""
    is_pull_request: bool = False


class EventRecord(BaseModel):
    """Record of a processed GitHub event in the database."""

    id: int | None = None
    event_id: str
    event_type: str
    repo: str
    issue_number: int
    author: str = ""
    score: int = 0
    action_taken: str = ""
    created_at: str = ""


class CommentRecord(BaseModel):
    """Record of a comment posted by the plugin."""

    id: int | None = None
    github_comment_id: int = 0
    repo: str = ""
    issue_number: int = 0
    content_hash: str = ""
    created_at: str = ""


class FileTreeEntry(BaseModel):
    """A file entry from the repository tree."""

    path: str
    sha: str = ""
    size: int = 0


class CachedFile(BaseModel):
    """A cached file content entry."""

    repo: str
    path: str
    sha: str
    content: str
    cached_at: str = ""


class CodeContext(BaseModel):
    """Assembled code context for answering a question."""

    repo: str
    question: str
    files: list[CachedFile] = Field(default_factory=list)
    total_size: int = 0


class DecisionResult(BaseModel):
    """Result of the decision engine's evaluation."""

    score: int
    action: EventAction
    factors: dict[str, int] = Field(default_factory=dict)


class PluginState(BaseModel):
    """Runtime state of the GitHub plugin."""

    events_processed: int = 0
    comments_posted: int = 0
    notifications_sent: int = 0
    repos_monitored: int = 0
    last_check: float | None = None
    rate_limit_remaining: int = 5000
    current_health: str = "nominal"


# ---------------------------------------------------------------------------
# Agentic models — world state observations (GitHub-specific)
# ---------------------------------------------------------------------------


class CIStatus(StrEnum):
    """Aggregated CI status for a commit/PR."""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    UNKNOWN = "unknown"


class VersionBumpType(StrEnum):
    """Semantic version bump type parsed from Dependabot PR titles."""

    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    UNKNOWN = "unknown"


class PRSnapshot(BaseModel):
    """Snapshot of a pull request at observation time."""

    number: int
    title: str
    author: str
    state: str = "open"  # open, closed
    draft: bool = False
    mergeable: bool = False
    merged: bool = False
    labels: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    head_sha: str = ""
    base_branch: str = "main"
    ci_status: CIStatus = CIStatus.UNKNOWN
    ci_details: list[dict[str, str]] = Field(default_factory=list)
    is_dependabot: bool = False
    version_bump: VersionBumpType = VersionBumpType.UNKNOWN
    review_state: str = ""  # approved, changes_requested, pending
    comments_count: int = 0
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0
    age_hours: float = 0.0


class IssueSnapshot(BaseModel):
    """Snapshot of an issue at observation time."""

    number: int
    title: str
    author: str
    state: str = "open"
    labels: list[str] = Field(default_factory=list)
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    comments_count: int = 0
    age_hours: float = 0.0
    has_our_response: bool = False


class RepoObservation(BaseModel):
    """Complete world-state snapshot for a single repository."""

    repo: str
    observed_at: str = ""
    open_prs: list[PRSnapshot] = Field(default_factory=list)
    open_issues: list[IssueSnapshot] = Field(default_factory=list)
    dependabot_prs: list[PRSnapshot] = Field(default_factory=list)
    failing_ci: list[PRSnapshot] = Field(default_factory=list)
    stale_prs: list[PRSnapshot] = Field(default_factory=list)
    unanswered_issues: list[IssueSnapshot] = Field(default_factory=list)
    repo_summary: str = ""
    file_count: int = 0


# ---------------------------------------------------------------------------
# GitHub-specific action types (string constants for handler registration)
# ---------------------------------------------------------------------------


class ActionType(StrEnum):
    """Types of actions the GitHub agent can take."""

    MERGE_PR = "merge_pr"
    APPROVE_PR = "approve_pr"
    REVIEW_PR = "review_pr"
    RESPOND_ISSUE = "respond_issue"
    NOTIFY_OWNER = "notify_owner"
    REFRESH_CONTEXT = "refresh_context"
    COMMENT_PR = "comment_pr"
    SKIP = "skip"
