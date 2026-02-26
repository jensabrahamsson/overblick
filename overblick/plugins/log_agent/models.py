"""
Data models for the log agent plugin.

Defines domain-specific types for log scanning, pattern matching,
and alert management.
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
    PlannedAction,
    TickLog,
)


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ActionType(str, Enum):
    """Actions the log agent can take."""
    SCAN_LOGS = "scan_logs"
    ANALYZE_PATTERN = "analyze_pattern"
    SEND_ALERT = "send_alert"
    SKIP = "skip"


class LogEntry(BaseModel):
    """A log entry found during scanning."""
    identity: str
    file_path: str
    line_number: int = 0
    level: str = "ERROR"
    message: str = ""
    traceback: str = ""
    timestamp: str = ""

    @property
    def source_ref(self) -> str:
        """Unique reference for deduplication."""
        return f"log:{self.identity}/{self.file_path}:{self.line_number}"


class LogScanResult(BaseModel):
    """Result of scanning one identity's logs."""
    identity: str
    errors_found: int = 0
    criticals_found: int = 0
    entries: list[LogEntry] = []
    scan_duration_ms: float = 0.0


class LogObservation(BaseModel):
    """Complete observation for the log agent."""
    scan_results: list[LogScanResult] = []
    total_errors: int = 0
    total_criticals: int = 0
    identities_scanned: int = 0
    audit_anomalies: list[dict[str, Any]] = []


class PluginState(BaseModel):
    """Runtime state of the log agent plugin."""
    scans_completed: int = 0
    alerts_sent: int = 0
    patterns_analyzed: int = 0
    last_check: Optional[float] = None
    current_health: str = "nominal"
