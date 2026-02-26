"""
Git workspace manager for the dev agent.

Manages an isolated git clone where the agent works. Handles clone,
branch creation, sync, commit, push, and cleanup. All operations use
asyncio subprocess for non-blocking execution.

SAFETY: commit_and_push() asserts current branch is NOT main.

NOTE: Uses asyncio.create_subprocess_exec (not shell exec) — safe
against command injection. All arguments are passed as separate tokens.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from overblick.plugins.dev_agent.models import WorkspaceState

logger = logging.getLogger(__name__)

# Maximum time for a single git operation (seconds)
_GIT_TIMEOUT = 120


class WorkspaceManager:
    """
    Manages the isolated git workspace for the dev agent.

    All git operations happen in a separate clone, never
    touching the user's working directory.
    """

    def __init__(
        self,
        workspace_path: Path,
        repo_url: str,
        default_branch: str = "main",
        dry_run: bool = True,
        git_author_name: str = "",
        git_author_email: str = "",
    ):
        self._path = workspace_path
        self._repo_url = repo_url
        self._default_branch = default_branch
        self._dry_run = dry_run
        self._git_author_name = git_author_name
        self._git_author_email = git_author_email

    @property
    def path(self) -> Path:
        return self._path

    @property
    def repo_url(self) -> str:
        return self._repo_url

    async def _configure_git_author(self) -> None:
        """Set local git author config in the workspace (if configured)."""
        if self._git_author_name:
            await self._run_git("config", "user.name", self._git_author_name)
            logger.info("Workspace git user.name set to: %s", self._git_author_name)
        if self._git_author_email:
            await self._run_git("config", "user.email", self._git_author_email)
            logger.info("Workspace git user.email set to: %s", self._git_author_email)

    async def get_state(self) -> WorkspaceState:
        """Get the current workspace state."""
        cloned = (self._path / ".git").is_dir()
        branch = ""
        clean = True

        if cloned:
            branch = await self._get_current_branch()
            clean = await self.is_clean()

        return WorkspaceState(
            cloned=cloned,
            current_branch=branch,
            is_clean=clean,
            repo_url=self._repo_url,
            workspace_path=str(self._path),
        )

    async def ensure_cloned(self) -> bool:
        """
        Ensure the repo is cloned. Clone if needed.

        Returns True if the workspace is ready.
        """
        if (self._path / ".git").is_dir():
            logger.debug("Workspace already cloned at %s", self._path)
            await self._configure_git_author()
            return True

        logger.info("Cloning %s to %s", self._repo_url, self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        ok, output = await self._run_git(
            "clone", self._repo_url, str(self._path),
            cwd=self._path.parent,
        )
        if not ok:
            logger.error("Clone failed: %s", output)
            return False

        # Configure git author identity (local to this workspace only)
        await self._configure_git_author()

        logger.info("Clone successful")
        return True

    async def sync_main(self) -> bool:
        """
        Checkout main and pull latest.

        Returns True on success.
        """
        branch = self._default_branch

        ok, _ = await self._run_git("checkout", branch)
        if not ok:
            logger.error("Failed to checkout %s", branch)
            return False

        ok, output = await self._run_git("pull", "origin", branch)
        if not ok:
            logger.warning("Pull failed (may be offline): %s", output)
            # Not fatal — we can work with what we have

        return True

    async def create_branch(self, name: str) -> bool:
        """
        Create and checkout a new branch from main.

        Returns True on success.
        """
        # Ensure we start from a clean main
        await self.sync_main()

        ok, output = await self._run_git("checkout", "-b", name)
        if not ok:
            # Branch may already exist — try switching to it
            ok, output = await self._run_git("checkout", name)
            if not ok:
                logger.error("Failed to create/switch branch %s: %s", name, output)
                return False

        logger.info("On branch %s", name)
        return True

    async def commit_and_push(self, message: str) -> bool:
        """
        Stage all changes, commit, and push.

        SAFETY: Refuses to push if on main branch.
        Returns True on success.
        """
        branch = await self._get_current_branch()

        # Safety check — NEVER commit to main
        if branch == self._default_branch:
            logger.error("SAFETY: Refusing to commit on %s", branch)
            return False

        if self._dry_run:
            logger.info("DRY RUN: would commit and push on branch %s", branch)
            return True

        # Stage all changes
        ok, _ = await self._run_git("add", "-A")
        if not ok:
            return False

        # Check if there are changes to commit
        if await self.is_clean():
            logger.warning("No changes to commit")
            return False

        # Commit
        ok, output = await self._run_git("commit", "-m", message)
        if not ok:
            logger.error("Commit failed: %s", output)
            return False

        # Push
        ok, output = await self._run_git("push", "-u", "origin", branch)
        if not ok:
            logger.error("Push failed: %s", output)
            return False

        logger.info("Committed and pushed to %s", branch)
        return True

    async def get_diff(self) -> str:
        """Get the current diff (staged + unstaged)."""
        _, output = await self._run_git("diff", "HEAD")
        return output

    async def is_clean(self) -> bool:
        """Check if the working tree is clean."""
        ok, output = await self._run_git("status", "--porcelain")
        return ok and not output.strip()

    async def cleanup_branch(self, branch: str) -> None:
        """Delete a local branch (and optionally remote)."""
        if branch == self._default_branch:
            logger.warning("Refusing to delete %s", branch)
            return

        # Switch to main first
        await self._run_git("checkout", self._default_branch)

        # Delete local
        await self._run_git("branch", "-D", branch)

        if not self._dry_run:
            # Delete remote
            await self._run_git("push", "origin", "--delete", branch)

        logger.info("Cleaned up branch %s", branch)

    async def list_branches(self) -> list[str]:
        """List all local branches."""
        ok, output = await self._run_git("branch", "--format=%(refname:short)")
        if not ok:
            return []
        return [b.strip() for b in output.strip().split("\n") if b.strip()]

    # ── Internal helpers ─────────────────────────────────────────────────

    async def _get_current_branch(self) -> str:
        """Get the current branch name."""
        ok, output = await self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return output.strip() if ok else ""

    async def _run_git(
        self,
        *args: str,
        cwd: Optional[Path] = None,
        timeout: int = _GIT_TIMEOUT,
    ) -> tuple[bool, str]:
        """
        Run a git command via create_subprocess_exec (no shell — safe).

        Returns (success, output) tuple.
        """
        cmd = ["git"] + list(args)
        work_dir = cwd or self._path

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            success = proc.returncode == 0

            if not success:
                logger.debug("git %s failed (rc=%d): %s", args[0], proc.returncode, output[:500])

            return success, output

        except asyncio.TimeoutError:
            logger.error("git %s timed out after %ds", args[0], timeout)
            if proc:
                proc.kill()
            return False, f"Timeout after {timeout}s"
        except FileNotFoundError:
            logger.error("git not found in PATH")
            return False, "git not found"
        except Exception as e:
            logger.error("git %s error: %s", args[0], e)
            return False, str(e)
