"""
Bug observer for the dev agent.

Collects bugs from multiple sources:
1. IPC queue (bug_report / log_alert messages from other agents)
2. Log file scanning (ERROR patterns and tracebacks)
3. Database (existing unresolved bugs)

Implements the Observer protocol for the agentic loop.
"""

import logging
from collections import deque
from typing import Any

from overblick.plugins.dev_agent.database import DevAgentDB
from overblick.plugins.dev_agent.log_watcher import LogWatcher
from overblick.plugins.dev_agent.models import (
    BugReport,
    BugSource,
    BugStatus,
    DevAgentObservation,
    WorkspaceState,
)

logger = logging.getLogger(__name__)


class BugObserver:
    """
    Collects world state for the dev agent.

    Drains IPC queue, scans logs, and queries the database
    for active bugs. Returns a DevAgentObservation.
    """

    def __init__(
        self,
        db: DevAgentDB,
        log_watcher: LogWatcher,
        workspace_state_fn=None,
    ):
        self._db = db
        self._log_watcher = log_watcher
        self._workspace_state_fn = workspace_state_fn
        self._ipc_queue: deque[dict] = deque(maxlen=100)

    def enqueue_ipc_message(self, msg_type: str, payload: dict) -> None:
        """
        Enqueue an IPC message for processing on next observe().

        Called by the plugin when receiving bug_report or log_alert messages.
        """
        self._ipc_queue.append({"type": msg_type, "payload": payload})

    async def observe(self) -> DevAgentObservation:
        """Collect and return the current world state."""
        bugs: list[BugReport] = []
        ipc_count = 0
        log_errors_found = 0

        # 1. Drain IPC queue
        while self._ipc_queue:
            msg = self._ipc_queue.popleft()
            ipc_count += 1
            bug = self._ipc_to_bug(msg)
            if bug:
                # Check for duplicates in DB
                existing = await self._db.get_bug_by_ref(
                    bug.source.value, bug.source_ref,
                )
                if not existing:
                    await self._db.upsert_bug(bug)
                    bugs.append(bug)

        # 2. Scan log files
        if self._log_watcher.enabled:
            log_bugs, log_errors_found = await self._scan_logs()
            bugs.extend(log_bugs)

        # 3. Get all active bugs from DB (includes newly added ones)
        active_bugs = await self._db.get_active_bugs()

        # 4. Get workspace state
        workspace = WorkspaceState()
        if self._workspace_state_fn:
            try:
                workspace = await self._workspace_state_fn()
            except Exception as e:
                logger.warning("Failed to get workspace state: %s", e)

        # 5. Get recent fix attempts
        recent_attempts = await self._db.get_recent_attempts(limit=5)

        # 6. Get pending PRs
        pr_bugs = await self._db.get_bugs_by_status(BugStatus.PR_CREATED.value)
        pending_prs = [b.pr_url for b in pr_bugs if b.pr_url]

        return DevAgentObservation(
            bugs=active_bugs,
            workspace=workspace,
            recent_fixes=recent_attempts,
            pending_prs=pending_prs,
            log_errors_found=log_errors_found,
            ipc_messages_received=ipc_count,
        )

    def format_for_planner(self, observation: Any) -> str:
        """Format observation as text for the LLM planner."""
        if not observation or not isinstance(observation, DevAgentObservation):
            return "No observations available."

        obs: DevAgentObservation = observation
        parts = []

        # Bug summary
        if obs.bugs:
            parts.append(f"## Active Bugs ({len(obs.bugs)})")
            for bug in obs.bugs[:10]:  # Cap at 10 for prompt size
                status_icon = {
                    BugStatus.NEW: "NEW",
                    BugStatus.ANALYZING: "ANALYZING",
                    BugStatus.FIXING: "FIXING",
                    BugStatus.TESTING: "TESTING",
                    BugStatus.PR_CREATED: "PR",
                    BugStatus.FAILED: "FAILED",
                }.get(bug.status, str(bug.status.value))

                parts.append(
                    f"- [{status_icon}] #{bug.id}: {bug.title} "
                    f"(priority={bug.priority}, attempts={bug.fix_attempts}/{bug.max_attempts}, "
                    f"source={bug.source.value})"
                )
                if bug.error_text:
                    parts.append(f"  Error: {bug.error_text[:200]}")
                if bug.analysis:
                    parts.append(f"  Analysis: {bug.analysis[:200]}")
        else:
            parts.append("## No active bugs")

        # Workspace state
        parts.append(f"\n## Workspace")
        ws = obs.workspace
        parts.append(
            f"- Cloned: {ws.cloned}, Branch: {ws.current_branch or 'N/A'}, "
            f"Clean: {ws.is_clean}"
        )

        # Recent activity
        if obs.recent_fixes:
            parts.append(f"\n## Recent Fix Attempts ({len(obs.recent_fixes)})")
            for attempt in obs.recent_fixes[:5]:
                status = "PASS" if attempt.tests_passed else "FAIL"
                parts.append(
                    f"- Bug #{attempt.bug_id}, attempt #{attempt.attempt_number}: "
                    f"tests={status}, committed={attempt.committed}"
                )

        # Pending PRs
        if obs.pending_prs:
            parts.append(f"\n## Pending PRs ({len(obs.pending_prs)})")
            for pr_url in obs.pending_prs:
                parts.append(f"- {pr_url}")

        # Summary
        if obs.log_errors_found:
            parts.append(f"\n## Log Scan: {obs.log_errors_found} new errors found")
        if obs.ipc_messages_received:
            parts.append(f"## IPC: {obs.ipc_messages_received} messages received")

        return "\n".join(parts)

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _scan_logs(self) -> tuple[list[BugReport], int]:
        """Scan log files and create bugs for new errors."""
        bugs: list[BugReport] = []
        total_errors = 0

        for identity, file_path in self._log_watcher.get_log_files():
            offset = await self._db.get_log_offset(str(file_path))
            errors, new_offset = self._log_watcher.scan_file(
                file_path, identity, offset,
            )

            if new_offset > offset:
                await self._db.update_log_offset(str(file_path), new_offset)

            if errors:
                unique_errors = LogWatcher.deduplicate_errors(errors)
                total_errors += len(unique_errors)

                for error in unique_errors:
                    # Check for duplicates
                    existing = await self._db.get_bug_by_ref(
                        BugSource.LOG_ERROR.value, error.source_ref,
                    )
                    if not existing:
                        bug = BugReport(
                            source=BugSource.LOG_ERROR,
                            source_ref=error.source_ref,
                            title=f"[{identity}] {error.message[:100]}",
                            description=f"Error in {error.file_path}",
                            error_text=error.traceback or error.message,
                            file_path=error.file_path,
                            identity=identity,
                            priority=70 if error.level == "CRITICAL" else 50,
                        )
                        await self._db.upsert_bug(bug)
                        bugs.append(bug)

        return bugs, total_errors

    @staticmethod
    def _ipc_to_bug(msg: dict) -> BugReport | None:
        """Convert an IPC message to a BugReport."""
        msg_type = msg.get("type", "")
        payload = msg.get("payload", {})

        if msg_type == "bug_report":
            return BugReport(
                source=BugSource.IPC_REPORT,
                source_ref=payload.get("ref", payload.get("issue_url", "")),
                title=payload.get("title", "Unknown bug"),
                description=payload.get("description", ""),
                error_text=payload.get("error_text", ""),
                file_path=payload.get("file_path", ""),
                priority=payload.get("priority", 60),
            )
        elif msg_type == "log_alert":
            return BugReport(
                source=BugSource.LOG_ERROR,
                source_ref=payload.get("ref", ""),
                title=payload.get("message", "Log alert"),
                description=payload.get("details", ""),
                error_text=payload.get("traceback", ""),
                file_path=payload.get("file_path", ""),
                identity=payload.get("identity", ""),
                priority=payload.get("priority", 50),
            )

        return None
