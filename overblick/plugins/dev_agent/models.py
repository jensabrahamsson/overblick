"""
Data models for the dev agent plugin.

Defines domain-specific structures for bug tracking, fix attempts,
workspace state, and observations. Generic agentic models are
re-exported from core for convenience.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

# Re-export core agentic models for convenience
from overblick.core.agentic.models import (  # noqa: F401
    ActionOutcome,
    ActionPlan,
    AgentGoal,
    AgentLearning,
    GoalStatus,
    PlannedAction,
    TickLog,
)


# ---------------------------------------------------------------------------
# Bug status lifecycle
# ---------------------------------------------------------------------------

class BugStatus(str, Enum):
    """Lifecycle status of a tracked bug."""
    NEW = "new"
    ANALYZING = "analyzing"
    FIXING = "fixing"
    TESTING = "testing"
    PR_CREATED = "pr_created"
    FIXED = "fixed"
    FAILED = "failed"
    SKIPPED = "skipped"


class BugSource(str, Enum):
    """Where a bug was discovered."""
    GITHUB_ISSUE = "github_issue"
    LOG_ERROR = "log_error"
    IPC_REPORT = "ipc_report"


# ---------------------------------------------------------------------------
# Bug report
# ---------------------------------------------------------------------------

class BugReport(BaseModel):
    """A tracked bug report."""
    id: Optional[int] = None
    source: BugSource
    source_ref: str = ""  # e.g. "issue#42", "log:anomal/agent.log:142"
    title: str
    description: str = ""
    error_text: str = ""  # Raw error/traceback text
    file_path: str = ""  # File where error occurred (if known)
    identity: str = ""  # Identity that produced the error (for log bugs)
    status: BugStatus = BugStatus.NEW
    priority: int = 50  # 0-100, higher = more important
    fix_attempts: int = 0
    max_attempts: int = 3
    branch_name: str = ""
    pr_url: str = ""
    analysis: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_retriable(self) -> bool:
        """Whether another fix attempt is allowed."""
        return (
            self.status in (BugStatus.NEW, BugStatus.ANALYZING, BugStatus.FAILED)
            and self.fix_attempts < self.max_attempts
        )

    @property
    def slug(self) -> str:
        """Short slug for branch naming."""
        words = self.title.lower().split()[:4]
        slug = "-".join(w for w in words if w.isalnum())
        return slug[:40] or "unknown"


# ---------------------------------------------------------------------------
# Fix attempt record
# ---------------------------------------------------------------------------

class FixAttempt(BaseModel):
    """Record of a single fix attempt."""
    id: Optional[int] = None
    bug_id: int
    attempt_number: int = 1
    analysis: str = ""
    files_changed: list[str] = []
    tests_passed: bool = False
    test_output: str = ""
    opencode_output: str = ""
    committed: bool = False
    branch_name: str = ""
    duration_seconds: float = 0.0
    created_at: str = ""


# ---------------------------------------------------------------------------
# Workspace state
# ---------------------------------------------------------------------------

class WorkspaceState(BaseModel):
    """Current state of the git workspace."""
    cloned: bool = False
    current_branch: str = ""
    is_clean: bool = True
    last_synced: str = ""
    repo_url: str = ""
    workspace_path: str = ""


# ---------------------------------------------------------------------------
# Log error entry
# ---------------------------------------------------------------------------

class LogErrorEntry(BaseModel):
    """An error found by the log watcher."""
    file_path: str
    line_number: int = 0
    identity: str = ""
    level: str = "ERROR"  # ERROR, CRITICAL, Traceback
    message: str = ""
    traceback: str = ""
    timestamp: str = ""

    @property
    def source_ref(self) -> str:
        """Generate a source reference string."""
        return f"log:{self.identity}/{self.file_path}:{self.line_number}"


# ---------------------------------------------------------------------------
# Opencode result
# ---------------------------------------------------------------------------

class OpencodeResult(BaseModel):
    """Parsed result from an opencode invocation."""
    success: bool = False
    output: str = ""
    files_changed: list[str] = []
    error: str = ""
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Test run result
# ---------------------------------------------------------------------------

class TestRunResult(BaseModel):
    """Result of running pytest in the workspace."""
    passed: bool = False
    total: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    output: str = ""
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

class DevAgentObservation(BaseModel):
    """Complete world-state snapshot for the dev agent."""
    bugs: list[BugReport] = []
    workspace: WorkspaceState = WorkspaceState()
    recent_fixes: list[FixAttempt] = []
    pending_prs: list[str] = []  # PR URLs
    log_errors_found: int = 0
    ipc_messages_received: int = 0


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """Types of actions the dev agent can take."""
    ANALYZE_BUG = "analyze_bug"
    FIX_BUG = "fix_bug"
    RUN_TESTS = "run_tests"
    CREATE_PR = "create_pr"
    NOTIFY_OWNER = "notify_owner"
    CLEAN_WORKSPACE = "clean_workspace"
    SKIP = "skip"
