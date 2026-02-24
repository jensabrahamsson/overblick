"""
Test runner for the dev agent.

Runs pytest in the workspace directory and parses the results.
Used to validate fixes before committing.

NOTE: Uses asyncio.create_subprocess_exec (not shell) — safe against
command injection. Test paths are passed as separate arguments.
"""

import asyncio
import logging
import re
import time
from pathlib import Path

from overblick.plugins.dev_agent.models import TestRunResult

logger = logging.getLogger(__name__)

# Maximum time for test run (5 minutes)
_TEST_TIMEOUT = 300


class TestRunner:
    """
    Runs pytest in the dev agent's workspace.

    Parses output to determine pass/fail status and extract
    summary statistics.
    """

    def __init__(
        self,
        workspace_path: Path,
        timeout: int = _TEST_TIMEOUT,
        dry_run: bool = True,
    ):
        self._workspace = workspace_path
        self._timeout = timeout
        self._dry_run = dry_run

    async def run_tests(self, test_path: str = "") -> TestRunResult:
        """
        Run pytest and return structured results.

        Args:
            test_path: Optional specific test path. If empty, runs full suite
                       (excluding LLM and E2E tests).
        """
        if self._dry_run:
            logger.info("DRY RUN: would run tests in %s", self._workspace)
            return TestRunResult(
                passed=True,
                output="DRY RUN: Tests would run here.",
            )

        cmd = self._build_command(test_path)
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self._workspace),
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )

            duration = time.monotonic() - start
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            passed = proc.returncode == 0

            result = self._parse_output(output)
            result.passed = passed
            result.duration_seconds = duration
            result.output = output[-5000:]  # Keep last 5000 chars

            logger.info(
                "Tests %s: %d passed, %d failed, %d errors (%.1fs)",
                "PASSED" if passed else "FAILED",
                result.total - result.failures - result.errors,
                result.failures, result.errors, duration,
            )

            return result

        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            logger.error("Tests timed out after %ds", self._timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return TestRunResult(
                passed=False,
                output=f"Test timed out after {self._timeout}s",
                duration_seconds=duration,
            )
        except FileNotFoundError:
            return TestRunResult(
                passed=False,
                output="pytest not found in PATH",
            )
        except Exception as e:
            return TestRunResult(
                passed=False,
                output=str(e),
            )

    def _build_command(self, test_path: str) -> list[str]:
        """Build the pytest command as a list of arguments."""
        cmd = [
            "python", "-m", "pytest",
            test_path or "tests/",
            "-v",
            "-m", "not llm and not e2e",
            "--tb=short",
            "-q",
        ]
        return cmd

    @staticmethod
    def _parse_output(output: str) -> TestRunResult:
        """Parse pytest output to extract statistics."""
        result = TestRunResult()

        # Match pytest summary line patterns:
        # "5 passed, 2 failed, 1 error in 3.45s"
        # "10 passed in 1.23s"
        summary_match = re.search(r"(\d+) passed", output)
        failed_match = re.search(r"(\d+) failed", output)
        error_match = re.search(r"(\d+) error", output)
        skipped_match = re.search(r"(\d+) skipped", output)

        passed_count = int(summary_match.group(1)) if summary_match else 0
        result.failures = int(failed_match.group(1)) if failed_match else 0
        result.errors = int(error_match.group(1)) if error_match else 0
        result.skipped = int(skipped_match.group(1)) if skipped_match else 0
        result.total = passed_count + result.failures + result.errors + result.skipped

        return result
