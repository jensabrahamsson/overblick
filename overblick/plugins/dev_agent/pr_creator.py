"""
Pull request creator for the dev agent.

Uses the `gh` CLI to create pull requests on GitHub.
Permission-gated — requires approval before creating PRs.

NOTE: Uses asyncio.create_subprocess_exec (not shell) — safe against
command injection. All arguments are passed as separate list elements.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from overblick.plugins.dev_agent.models import BugReport

logger = logging.getLogger(__name__)

# Timeout for gh CLI operations
_GH_TIMEOUT = 60


class PRCreator:
    """
    Creates GitHub pull requests using the gh CLI.

    All operations are permission-gated and support dry-run mode.
    """

    def __init__(
        self,
        workspace_path: Path,
        default_branch: str = "main",
        dry_run: bool = True,
    ):
        self._workspace = workspace_path
        self._default_branch = default_branch
        self._dry_run = dry_run

    async def create_pr(
        self,
        bug: BugReport,
        branch: str,
        files_changed: list[str] | None = None,
        test_summary: str = "",
    ) -> Optional[str]:
        """
        Create a pull request for a bug fix.

        Args:
            bug: The bug being fixed
            branch: The branch with the fix
            files_changed: List of files modified
            test_summary: Test run summary

        Returns:
            PR URL on success, None on failure.
        """
        title = f"fix: {bug.title}"
        if len(title) > 70:
            title = title[:67] + "..."

        body = self._build_pr_body(bug, files_changed or [], test_summary)

        if self._dry_run:
            logger.info("DRY RUN: would create PR '%s' from %s", title, branch)
            return f"https://github.com/dry-run/pr/{branch}"

        return await self._run_gh_pr_create(title, body, branch)

    async def _run_gh_pr_create(
        self, title: str, body: str, branch: str,
    ) -> Optional[str]:
        """Run `gh pr create` via create_subprocess_exec (no shell — safe)."""
        cmd = [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", self._default_branch,
            "--head", branch,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GH_TIMEOUT,
            )

            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            if proc.returncode != 0:
                logger.error("gh pr create failed: %s", stderr_text or stdout_text)
                return None

            # gh pr create outputs the PR URL
            pr_url = stdout_text.strip()
            if pr_url.startswith("https://"):
                logger.info("Created PR: %s", pr_url)
                return pr_url

            logger.warning("Unexpected gh output: %s", stdout_text[:500])
            return stdout_text.strip() or None

        except asyncio.TimeoutError:
            logger.error("gh pr create timed out")
            return None
        except FileNotFoundError:
            logger.error("gh CLI not found in PATH")
            return None
        except Exception as e:
            logger.error("gh pr create error: %s", e)
            return None

    @staticmethod
    def _build_pr_body(
        bug: BugReport,
        files_changed: list[str],
        test_summary: str,
    ) -> str:
        """Build the PR description body."""
        parts = [
            "## Summary",
            f"Automated fix for: **{bug.title}**",
            "",
            f"- Source: `{bug.source.value}` ({bug.source_ref})",
            f"- Priority: {bug.priority}",
            f"- Fix attempt: #{bug.fix_attempts}",
        ]

        if bug.description:
            parts.extend(["", "### Bug Description", bug.description[:1000]])

        if files_changed:
            parts.extend(["", "### Files Changed"])
            for f in files_changed[:20]:
                parts.append(f"- `{f}`")

        if test_summary:
            parts.extend(["", "### Test Results", test_summary[:1000]])

        parts.extend([
            "",
            "---",
            "*Automated PR by Smed (Overblick Dev Agent)*",
        ])

        return "\n".join(parts)
