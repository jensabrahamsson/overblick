"""
Pydantic models for the GitHub monitoring plugin.

Defines data structures for GitHub events, code context,
decision actions, and plugin state.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class EventType(str, Enum):
    """Types of GitHub events the plugin tracks."""
    ISSUE_OPENED = "issue_opened"
    ISSUE_COMMENT = "issue_comment"
    MENTION = "mention"


class EventAction(str, Enum):
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
    labels: list[str] = []
    created_at: str = ""
    is_pull_request: bool = False


class EventRecord(BaseModel):
    """Record of a processed GitHub event in the database."""
    id: Optional[int] = None
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
    id: Optional[int] = None
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
    files: list[CachedFile] = []
    total_size: int = 0


class DecisionResult(BaseModel):
    """Result of the decision engine's evaluation."""
    score: int
    action: EventAction
    factors: dict[str, int] = {}


class PluginState(BaseModel):
    """Runtime state of the GitHub plugin."""
    events_processed: int = 0
    comments_posted: int = 0
    notifications_sent: int = 0
    repos_monitored: int = 0
    last_check: Optional[float] = None
    rate_limit_remaining: int = 5000
    current_health: str = "nominal"
