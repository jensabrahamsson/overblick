"""
Tests for GitHub agent owner commands — parsing, queue management, and planner formatting.

Tests cover:
- Command parsing (valid commands, invalid input, edge cases)
- OwnerCommandQueue (fetch, dedup, format)
- Integration with TelegramNotifier.fetch_updates()
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.plugins.github.owner_commands import (
    OwnerCommand,
    OwnerCommandQueue,
)


# ---------------------------------------------------------------------------
# parse_command — static parsing
# ---------------------------------------------------------------------------

class TestParseCommand:
    """Tests for OwnerCommandQueue.parse_command() — pure function."""

    def test_merge_command(self):
        """Parse 'merge owner/repo#123'."""
        cmd = OwnerCommandQueue.parse_command("merge owner/repo#123")
        assert cmd is not None
        assert cmd.verb == "merge"
        assert cmd.repo == "owner/repo"
        assert cmd.number == 123
        assert cmd.action_type == "merge_pr"
        assert cmd.args == ""

    def test_close_command(self):
        """Parse 'close owner/repo#456'."""
        cmd = OwnerCommandQueue.parse_command("close owner/repo#456")
        assert cmd is not None
        assert cmd.verb == "close"
        assert cmd.repo == "owner/repo"
        assert cmd.number == 456
        assert cmd.action_type == "close_item"

    def test_approve_command(self):
        """Parse 'approve owner/repo#789'."""
        cmd = OwnerCommandQueue.parse_command("approve owner/repo#789")
        assert cmd is not None
        assert cmd.verb == "approve"
        assert cmd.action_type == "approve_pr"

    def test_review_command(self):
        """Parse 'review owner/repo#42'."""
        cmd = OwnerCommandQueue.parse_command("review owner/repo#42")
        assert cmd is not None
        assert cmd.verb == "review"
        assert cmd.action_type == "review_pr"

    def test_label_command_single(self):
        """Parse 'label owner/repo#10 bug'."""
        cmd = OwnerCommandQueue.parse_command("label owner/repo#10 bug")
        assert cmd is not None
        assert cmd.verb == "label"
        assert cmd.number == 10
        assert cmd.args == "bug"
        assert cmd.action_type == "label_item"

    def test_label_command_multiple(self):
        """Parse 'label owner/repo#10 bug,enhancement'."""
        cmd = OwnerCommandQueue.parse_command("label owner/repo#10 bug,enhancement")
        assert cmd is not None
        assert cmd.args == "bug,enhancement"

    def test_case_insensitive(self):
        """Commands are case-insensitive."""
        cmd = OwnerCommandQueue.parse_command("MERGE owner/repo#123")
        assert cmd is not None
        assert cmd.verb == "merge"

    def test_repo_with_dots_and_hyphens(self):
        """Repo names with dots and hyphens work."""
        cmd = OwnerCommandQueue.parse_command("merge my-org/my-repo.js#42")
        assert cmd is not None
        assert cmd.repo == "my-org/my-repo.js"
        assert cmd.number == 42

    def test_invalid_no_verb(self):
        """Invalid input without known verb returns None."""
        assert OwnerCommandQueue.parse_command("hello world") is None

    def test_invalid_no_number(self):
        """Command without PR/issue number returns None."""
        assert OwnerCommandQueue.parse_command("merge owner/repo") is None

    def test_invalid_unknown_verb(self):
        """Unknown verb returns None."""
        assert OwnerCommandQueue.parse_command("deploy owner/repo#123") is None

    def test_empty_string(self):
        """Empty string returns None."""
        assert OwnerCommandQueue.parse_command("") is None

    def test_none_text(self):
        """None text returns None."""
        assert OwnerCommandQueue.parse_command(None) is None

    def test_whitespace_only(self):
        """Whitespace-only returns None."""
        assert OwnerCommandQueue.parse_command("   ") is None

    def test_message_id_and_timestamp_preserved(self):
        """Message ID and timestamp are stored in the command."""
        cmd = OwnerCommandQueue.parse_command(
            "merge owner/repo#1", message_id=42, timestamp="2026-02-26T03:00:00Z",
        )
        assert cmd is not None
        assert cmd.message_id == 42
        assert cmd.timestamp == "2026-02-26T03:00:00Z"


# ---------------------------------------------------------------------------
# OwnerCommandQueue — fetch, dedup, format
# ---------------------------------------------------------------------------

class TestOwnerCommandQueue:
    """Tests for OwnerCommandQueue fetch/format lifecycle."""

    @pytest.mark.asyncio
    async def test_fetch_parses_commands_from_updates(self):
        """fetch_commands() parses Telegram updates into commands."""
        notifier = AsyncMock()
        update1 = MagicMock(message_id=1, text="merge owner/repo#10", timestamp="")
        update2 = MagicMock(message_id=2, text="close owner/repo#20", timestamp="")
        notifier.fetch_updates = AsyncMock(return_value=[update1, update2])

        queue = OwnerCommandQueue()
        commands = await queue.fetch_commands(notifier)

        assert len(commands) == 2
        assert commands[0].verb == "merge"
        assert commands[1].verb == "close"
        assert len(queue.pending_commands) == 2

    @pytest.mark.asyncio
    async def test_fetch_ignores_non_commands(self):
        """Non-command messages are ignored."""
        notifier = AsyncMock()
        update1 = MagicMock(message_id=1, text="merge owner/repo#10", timestamp="")
        update2 = MagicMock(message_id=2, text="hello, how are you?", timestamp="")
        notifier.fetch_updates = AsyncMock(return_value=[update1, update2])

        queue = OwnerCommandQueue()
        commands = await queue.fetch_commands(notifier)

        assert len(commands) == 1
        assert commands[0].verb == "merge"

    @pytest.mark.asyncio
    async def test_fetch_deduplicates_by_message_id(self):
        """Same message_id is not processed twice."""
        notifier = AsyncMock()
        update = MagicMock(message_id=1, text="merge owner/repo#10", timestamp="")
        notifier.fetch_updates = AsyncMock(return_value=[update])

        queue = OwnerCommandQueue()
        commands1 = await queue.fetch_commands(notifier)
        commands2 = await queue.fetch_commands(notifier)

        assert len(commands1) == 1
        assert len(commands2) == 0  # Already processed

    @pytest.mark.asyncio
    async def test_fetch_handles_no_notifier(self):
        """Returns empty list when notifier is None."""
        queue = OwnerCommandQueue()
        commands = await queue.fetch_commands(None)
        assert commands == []

    @pytest.mark.asyncio
    async def test_fetch_handles_notifier_error(self):
        """Gracefully handles fetch_updates exceptions."""
        notifier = AsyncMock()
        notifier.fetch_updates = AsyncMock(side_effect=RuntimeError("Network error"))

        queue = OwnerCommandQueue()
        commands = await queue.fetch_commands(notifier)
        assert commands == []

    def test_pop_commands_clears_queue(self):
        """pop_commands() returns and clears pending commands."""
        queue = OwnerCommandQueue()
        queue.pending_commands = [
            OwnerCommand(verb="merge", repo="owner/repo", number=10),
            OwnerCommand(verb="close", repo="owner/repo", number=20),
        ]

        popped = queue.pop_commands()
        assert len(popped) == 2
        assert len(queue.pending_commands) == 0

    def test_pop_commands_empty(self):
        """pop_commands() returns empty list when no pending."""
        queue = OwnerCommandQueue()
        assert queue.pop_commands() == []

    def test_format_empty_returns_empty_string(self):
        """format_for_planner() returns empty string when no commands."""
        queue = OwnerCommandQueue()
        assert queue.format_for_planner() == ""

    def test_format_single_command(self):
        """format_for_planner() formats a single command."""
        queue = OwnerCommandQueue()
        queue.pending_commands = [
            OwnerCommand(verb="merge", repo="owner/repo", number=42),
        ]

        text = queue.format_for_planner()
        assert "PENDING OWNER COMMANDS" in text
        assert "highest priority" in text
        assert "merge owner/repo#42" in text

    def test_format_multiple_commands(self):
        """format_for_planner() formats multiple commands."""
        queue = OwnerCommandQueue()
        queue.pending_commands = [
            OwnerCommand(verb="merge", repo="owner/repo", number=42),
            OwnerCommand(verb="label", repo="owner/repo", number=10, args="bug"),
        ]

        text = queue.format_for_planner()
        assert "merge owner/repo#42" in text
        assert "label owner/repo#10" in text
        assert "(bug)" in text

    def test_format_command_without_args(self):
        """Commands without args have no parenthesized suffix."""
        queue = OwnerCommandQueue()
        queue.pending_commands = [
            OwnerCommand(verb="merge", repo="owner/repo", number=1),
        ]

        text = queue.format_for_planner()
        assert "()" not in text
