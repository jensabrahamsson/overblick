"""
Dev agent action handlers — domain-specific ActionHandler implementations.

Each handler wraps dev-agent infrastructure (workspace, opencode, test runner,
PR creator) to implement the core ActionHandler protocol.

Provides build_dev_agent_handlers() factory to create the handler dict
for the core ActionExecutor.
"""

import logging
import time
from typing import Any

from overblick.core.agentic.models import ActionOutcome, PlannedAction
from overblick.plugins.dev_agent.database import DevAgentDB
from overblick.plugins.dev_agent.models import (
    ActionType,
    BugReport,
    BugStatus,
    FixAttempt,
)
from overblick.plugins.dev_agent.opencode_runner import OpencodeRunner
from overblick.plugins.dev_agent.pr_creator import PRCreator
from overblick.plugins.dev_agent.test_runner import TestRunner
from overblick.plugins.dev_agent.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


def _find_bug(action: PlannedAction, observation: Any) -> BugReport | None:
    """Find a bug in the observation by target_number (bug ID)."""
    if not observation or not hasattr(observation, "bugs"):
        return None
    for bug in observation.bugs:
        if bug.id == action.target_number:
            return bug
    return None


class AnalyzeBugHandler:
    """Handle analyze_bug actions — read-only analysis via opencode."""

    def __init__(self, db: DevAgentDB, opencode: OpencodeRunner):
        self._db = db
        self._opencode = opencode

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        bug = _find_bug(action, observation)
        if not bug:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{action.target_number} not found in observations",
            )

        if not bug.is_retriable:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{bug.id} is not retriable (status={bug.status.value}, attempts={bug.fix_attempts})",
            )

        # Update status
        await self._db.update_bug_status(bug.id, BugStatus.ANALYZING.value)

        # Run analysis
        analysis = await self._opencode.analyze_bug(bug)

        # Store analysis
        await self._db.update_bug_status(
            bug.id, BugStatus.NEW.value, analysis=analysis,
        )

        return ActionOutcome(
            action=action, success=True,
            result=f"Analyzed bug #{bug.id}: {analysis[:500]}",
        )


class FixBugHandler:
    """
    Handle fix_bug actions — the full fix pipeline.

    Pipeline: sync_main -> create_branch -> opencode fix -> test -> commit
    """

    def __init__(
        self,
        db: DevAgentDB,
        workspace: WorkspaceManager,
        opencode: OpencodeRunner,
        test_runner: TestRunner,
        dry_run: bool = True,
    ):
        self._db = db
        self._workspace = workspace
        self._opencode = opencode
        self._test_runner = test_runner
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        bug = _find_bug(action, observation)
        if not bug:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{action.target_number} not found in observations",
            )

        if not bug.is_retriable:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{bug.id} is not retriable (status={bug.status.value}, attempts={bug.fix_attempts})",
            )

        start = time.monotonic()
        attempt_number = bug.fix_attempts + 1
        branch_name = f"fix/{bug.id}-{bug.slug}"

        # Update status
        await self._db.update_bug_status(
            bug.id, BugStatus.FIXING.value,
            fix_attempts=str(attempt_number),
            branch_name=branch_name,
        )

        # 1. Ensure workspace is cloned
        if not await self._workspace.ensure_cloned():
            return self._fail(action, bug, attempt_number, start, "Failed to clone workspace")

        # 2. Create branch
        if not await self._workspace.create_branch(branch_name):
            return self._fail(action, bug, attempt_number, start, f"Failed to create branch {branch_name}")

        # 3. Run opencode fix
        analysis = bug.analysis or ""
        fix_result = await self._opencode.fix_bug(bug, analysis)
        if not fix_result.success:
            return self._fail(action, bug, attempt_number, start, f"opencode fix failed: {fix_result.error}")

        # 4. Run tests
        await self._db.update_bug_status(bug.id, BugStatus.TESTING.value)
        test_result = await self._test_runner.run_tests()

        duration = time.monotonic() - start

        # Record the attempt
        attempt = FixAttempt(
            bug_id=bug.id,
            attempt_number=attempt_number,
            analysis=analysis,
            files_changed=fix_result.files_changed,
            tests_passed=test_result.passed,
            test_output=test_result.output[-2000:],
            opencode_output=fix_result.output[:2000],
            branch_name=branch_name,
            duration_seconds=duration,
        )

        if not test_result.passed:
            await self._db.record_fix_attempt(attempt)
            status = BugStatus.FAILED.value if attempt_number >= bug.max_attempts else BugStatus.NEW.value
            await self._db.update_bug_status(bug.id, status)
            return ActionOutcome(
                action=action, success=False,
                error=f"Tests failed after fix (attempt {attempt_number}/{bug.max_attempts})",
                duration_ms=duration * 1000,
            )

        # 5. Commit and push
        commit_msg = f"fix: {bug.title}\n\nBug #{bug.id} ({bug.source.value})\nAutomated fix by Smed"
        committed = await self._workspace.commit_and_push(commit_msg)
        attempt.committed = committed
        await self._db.record_fix_attempt(attempt)

        if committed:
            await self._db.update_bug_status(
                bug.id, BugStatus.FIXING.value, branch_name=branch_name,
            )
            return ActionOutcome(
                action=action, success=True,
                result=f"Fixed bug #{bug.id} on branch {branch_name} (attempt {attempt_number})",
                duration_ms=duration * 1000,
            )
        else:
            return ActionOutcome(
                action=action, success=True,
                result=f"Fix applied and tests pass for bug #{bug.id} (dry_run or no changes to commit)",
                duration_ms=duration * 1000,
            )

    def _fail(
        self,
        action: PlannedAction,
        bug: BugReport,
        attempt_number: int,
        start: float,
        error: str,
    ) -> ActionOutcome:
        """Record a failed attempt and return failure outcome."""
        duration = time.monotonic() - start
        logger.warning("Fix attempt %d for bug #%d failed: %s", attempt_number, bug.id, error)
        return ActionOutcome(
            action=action, success=False,
            error=error,
            duration_ms=duration * 1000,
        )


class RunTestsHandler:
    """Handle run_tests actions — run pytest in the workspace."""

    def __init__(self, test_runner: TestRunner):
        self._test_runner = test_runner

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        test_path = action.params.get("test_path", "")
        result = await self._test_runner.run_tests(test_path)

        return ActionOutcome(
            action=action,
            success=result.passed,
            result=(
                f"Tests {'PASSED' if result.passed else 'FAILED'}: "
                f"{result.total} total, {result.failures} failures, "
                f"{result.errors} errors ({result.duration_seconds:.1f}s)"
            ),
            error="" if result.passed else result.output[-500:],
            duration_ms=result.duration_seconds * 1000,
        )


class CreatePRHandler:
    """Handle create_pr actions — create a GitHub PR for a fix."""

    def __init__(self, db: DevAgentDB, pr_creator: PRCreator, dry_run: bool = True):
        self._db = db
        self._pr_creator = pr_creator
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        bug = _find_bug(action, observation)
        if not bug:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{action.target_number} not found in observations",
            )

        if not bug.branch_name:
            return ActionOutcome(
                action=action, success=False,
                error=f"Bug #{bug.id} has no branch — fix it first",
            )

        # Get fix attempt info
        attempts = await self._db.get_fix_attempts(bug.id)
        latest = attempts[-1] if attempts else None

        files_changed = latest.files_changed if latest else []
        test_summary = ""
        if latest and latest.tests_passed:
            test_summary = f"All tests passed ({latest.test_output[-200:]})"

        pr_url = await self._pr_creator.create_pr(
            bug=bug,
            branch=bug.branch_name,
            files_changed=files_changed,
            test_summary=test_summary,
        )

        if pr_url:
            await self._db.update_bug_status(
                bug.id, BugStatus.PR_CREATED.value, pr_url=pr_url,
            )
            return ActionOutcome(
                action=action, success=True,
                result=f"Created PR for bug #{bug.id}: {pr_url}",
            )

        return ActionOutcome(
            action=action, success=False,
            error=f"Failed to create PR for bug #{bug.id}",
        )


class NotifyOwnerHandler:
    """Handle notify_owner actions — send Telegram notifications."""

    def __init__(self, notify_fn=None, dry_run: bool = True):
        self._notify_fn = notify_fn
        self._dry_run = dry_run

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        message = (
            f"*Smed (Dev Agent)*\n"
            f"{action.target}\n\n"
            f"_{action.reasoning}_"
        )

        if self._dry_run:
            logger.info("DRY RUN: would notify owner: %s", message[:200])
            return ActionOutcome(
                action=action, success=True,
                result=f"DRY RUN: would notify owner about {action.target}",
            )

        if self._notify_fn:
            try:
                await self._notify_fn(message)
                return ActionOutcome(
                    action=action, success=True,
                    result=f"Notified owner about {action.target}",
                )
            except Exception as e:
                return ActionOutcome(
                    action=action, success=False,
                    error=f"Notification failed: {e}",
                )

        return ActionOutcome(
            action=action, success=False,
            error="No notification function available",
        )


class CleanWorkspaceHandler:
    """Handle clean_workspace actions — prune old branches."""

    def __init__(self, workspace: WorkspaceManager):
        self._workspace = workspace

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        branch = action.params.get("branch", "")
        if branch:
            await self._workspace.cleanup_branch(branch)
            return ActionOutcome(
                action=action, success=True,
                result=f"Cleaned up branch {branch}",
            )

        # Clean all fix/ branches that are no longer needed
        branches = await self._workspace.list_branches()
        cleaned = 0
        for b in branches:
            if b.startswith("fix/") and b != await self._workspace._get_current_branch():
                await self._workspace.cleanup_branch(b)
                cleaned += 1

        return ActionOutcome(
            action=action, success=True,
            result=f"Cleaned up {cleaned} old fix branches",
        )


class SkipHandler:
    """Handle skip actions."""

    async def handle(self, action: PlannedAction, observation: Any) -> ActionOutcome:
        return ActionOutcome(
            action=action, success=True,
            result=f"Skipped: {action.reasoning}",
        )


def build_dev_agent_handlers(
    db: DevAgentDB,
    workspace: WorkspaceManager,
    opencode: OpencodeRunner,
    test_runner: TestRunner,
    pr_creator: PRCreator,
    notify_fn=None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Build the complete handler dict for the dev agent.

    Returns a dict mapping ActionType string values to handler instances.
    """
    return {
        ActionType.ANALYZE_BUG.value: AnalyzeBugHandler(db, opencode),
        ActionType.FIX_BUG.value: FixBugHandler(db, workspace, opencode, test_runner, dry_run),
        ActionType.RUN_TESTS.value: RunTestsHandler(test_runner),
        ActionType.CREATE_PR.value: CreatePRHandler(db, pr_creator, dry_run),
        ActionType.NOTIFY_OWNER.value: NotifyOwnerHandler(notify_fn, dry_run),
        ActionType.CLEAN_WORKSPACE.value: CleanWorkspaceHandler(workspace),
        ActionType.SKIP.value: SkipHandler(),
    }
