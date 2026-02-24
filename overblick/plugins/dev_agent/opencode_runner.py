"""
Opencode CLI runner for the dev agent.

Invokes `opencode run` with the Devstral 2 model to analyze and fix bugs.
Parses JSON output from opencode for structured results.

NOTE: Uses asyncio.create_subprocess_exec (not shell) — safe against
command injection. All arguments are passed as separate list elements.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from overblick.plugins.dev_agent.models import BugReport, OpencodeResult

logger = logging.getLogger(__name__)

# Default timeout for opencode invocations (10 minutes)
_DEFAULT_TIMEOUT = 600


class OpencodeRunner:
    """
    Invokes opencode CLI to analyze and fix bugs.

    Uses `opencode run --format json` for structured output.
    All invocations happen in the workspace directory.
    """

    def __init__(
        self,
        workspace_path: Path,
        model: str = "lmstudio/devstral-2-123b-iq5",
        timeout: int = _DEFAULT_TIMEOUT,
        dry_run: bool = True,
    ):
        self._workspace = workspace_path
        self._model = model
        self._timeout = timeout
        self._dry_run = dry_run

    async def analyze_bug(self, bug: BugReport) -> str:
        """
        Analyze a bug using opencode (read-only).

        Returns analysis text describing the root cause and
        suggested fix approach.
        """
        prompt = self._build_analysis_prompt(bug)

        if self._dry_run:
            logger.info("DRY RUN: would analyze bug '%s'", bug.title)
            return f"DRY RUN: Analysis of '{bug.title}' — opencode would analyze this bug."

        result = await self._run_opencode(prompt)
        if not result.success:
            logger.warning("Bug analysis failed: %s", result.error)
            return f"Analysis failed: {result.error}"

        return result.output

    async def fix_bug(self, bug: BugReport, analysis: str) -> OpencodeResult:
        """
        Fix a bug using opencode.

        Returns structured result with output and files changed.
        """
        prompt = self._build_fix_prompt(bug, analysis)

        if self._dry_run:
            logger.info("DRY RUN: would fix bug '%s'", bug.title)
            return OpencodeResult(
                success=True,
                output=f"DRY RUN: Fix for '{bug.title}' — opencode would fix this bug.",
            )

        return await self._run_opencode(prompt)

    async def _run_opencode(self, prompt: str) -> OpencodeResult:
        """
        Run opencode via create_subprocess_exec (no shell — safe).

        Returns parsed OpencodeResult.
        """
        cmd = [
            "opencode", "run",
            "--dir", str(self._workspace),
            "--model", self._model,
            "--format", "json",
            prompt,
        ]

        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )

            duration = time.monotonic() - start
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            if proc.returncode != 0:
                error_msg = stderr_text or stdout_text or f"Exit code {proc.returncode}"
                logger.warning("opencode failed (rc=%d): %s", proc.returncode, error_msg[:500])
                return OpencodeResult(
                    success=False,
                    error=error_msg[:2000],
                    duration_seconds=duration,
                )

            # Parse JSON output
            return self._parse_output(stdout_text, duration)

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.error("opencode timed out after %ds", self._timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return OpencodeResult(
                success=False,
                error=f"Timeout after {self._timeout}s",
                duration_seconds=duration,
            )
        except FileNotFoundError:
            return OpencodeResult(
                success=False,
                error="opencode not found in PATH",
            )
        except Exception as e:
            duration = time.monotonic() - start
            logger.error("opencode error: %s", e)
            return OpencodeResult(
                success=False,
                error=str(e),
                duration_seconds=duration,
            )

    def _parse_output(self, raw: str, duration: float) -> OpencodeResult:
        """Parse opencode JSON output."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fall back to treating raw output as plain text
            return OpencodeResult(
                success=True,
                output=raw[:10000],
                duration_seconds=duration,
            )

        # Extract fields from opencode's JSON format
        output_text = ""
        files_changed: list[str] = []

        if isinstance(data, dict):
            output_text = data.get("output", data.get("result", data.get("message", "")))
            if isinstance(output_text, dict):
                output_text = json.dumps(output_text)
            files_changed = data.get("files_changed", data.get("files", []))
            if isinstance(files_changed, str):
                files_changed = [files_changed]
        elif isinstance(data, str):
            output_text = data

        return OpencodeResult(
            success=True,
            output=str(output_text)[:10000],
            files_changed=files_changed,
            duration_seconds=duration,
        )

    @staticmethod
    def _build_analysis_prompt(bug: BugReport) -> str:
        """Build the analysis prompt for opencode."""
        parts = [
            "Analyze this bug and identify the root cause. Do NOT make any code changes.",
            f"\nBug title: {bug.title}",
        ]
        if bug.description:
            parts.append(f"\nDescription: {bug.description}")
        if bug.error_text:
            parts.append(f"\nError/Traceback:\n```\n{bug.error_text[:3000]}\n```")
        if bug.file_path:
            parts.append(f"\nSuspected file: {bug.file_path}")

        parts.append(
            "\nProvide:\n"
            "1. Root cause analysis\n"
            "2. Which file(s) need to be changed\n"
            "3. Suggested fix approach"
        )
        return "\n".join(parts)

    @staticmethod
    def _build_fix_prompt(bug: BugReport, analysis: str) -> str:
        """Build the fix prompt for opencode."""
        parts = [
            "Fix this bug. Make minimal, focused changes.",
            f"\nBug title: {bug.title}",
        ]
        if bug.description:
            parts.append(f"\nDescription: {bug.description}")
        if bug.error_text:
            parts.append(f"\nError/Traceback:\n```\n{bug.error_text[:3000]}\n```")
        if bug.file_path:
            parts.append(f"\nSuspected file: {bug.file_path}")
        if analysis:
            parts.append(f"\nPrevious analysis:\n{analysis[:2000]}")

        parts.append(
            "\nRequirements:\n"
            "1. Make minimal changes to fix the bug\n"
            "2. Do not refactor unrelated code\n"
            "3. Ensure the fix handles edge cases\n"
            "4. Add or update tests if appropriate"
        )
        return "\n".join(parts)
