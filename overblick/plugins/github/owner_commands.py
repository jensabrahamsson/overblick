"""
Owner command parser for the GitHub agent.

Parses Telegram messages into structured commands that the agentic planner
can prioritize. Commands from the owner always override the agent's default
planning logic.

Supported command formats:
    merge owner/repo#123
    close owner/repo#456
    approve owner/repo#789
    review owner/repo#42
    label owner/repo#10 bug
    label owner/repo#10 bug,enhancement

Security: Only messages from the configured Telegram chat_id are processed.
The TelegramNotifier capability handles chat_id filtering internally.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Command pattern: <verb> [repo#number] [args...]
# Examples:
#   merge owner/repo#123
#   close owner/repo#456
#   label owner/repo#10 bug
#   review owner/repo#42
_COMMAND_PATTERN = re.compile(
    r"^(?P<verb>merge|close|approve|review|label)"
    r"\s+(?P<repo>[\w\-]+/[\w\-.]+)"
    r"#(?P<number>\d+)"
    r"(?:\s+(?P<args>.+))?$",
    re.IGNORECASE,
)

# Valid command verbs and their mapping to action types
_VERB_TO_ACTION: dict[str, str] = {
    "merge": "merge_pr",
    "close": "close_item",
    "approve": "approve_pr",
    "review": "review_pr",
    "label": "label_item",
}


@dataclass
class OwnerCommand:
    """A parsed owner command from Telegram."""
    verb: str
    repo: str
    number: int
    args: str = ""
    action_type: str = ""
    raw_text: str = ""
    message_id: int = 0
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.action_type:
            self.action_type = _VERB_TO_ACTION.get(self.verb.lower(), self.verb)


@dataclass
class OwnerCommandQueue:
    """
    Fetches, parses, and formats owner commands for the planner.

    Integrates with TelegramNotifier.fetch_updates() to receive messages,
    parses them into OwnerCommand objects, and formats them as planning
    context that the agentic planner injects into its prompt.
    """
    pending_commands: list[OwnerCommand] = field(default_factory=list)
    processed_message_ids: set[int] = field(default_factory=set)
    _max_processed_ids: int = field(default=10_000, repr=False)

    @staticmethod
    def parse_command(text: str, message_id: int = 0, timestamp: str = "") -> Optional[OwnerCommand]:
        """Parse a single text message into an OwnerCommand, or None if not a command."""
        if not text:
            return None

        text = text.strip()
        match = _COMMAND_PATTERN.match(text)
        if not match:
            return None

        verb = match.group("verb").lower()
        repo = match.group("repo")
        number = int(match.group("number"))
        args = (match.group("args") or "").strip()

        return OwnerCommand(
            verb=verb,
            repo=repo,
            number=number,
            args=args,
            raw_text=text,
            message_id=message_id,
            timestamp=timestamp,
        )

    async def fetch_commands(self, notifier: object) -> list[OwnerCommand]:
        """
        Fetch new Telegram messages and parse any commands.

        Args:
            notifier: TelegramNotifier capability (must have fetch_updates method).

        Returns:
            List of newly parsed OwnerCommand objects.
        """
        if not notifier or not hasattr(notifier, "fetch_updates"):
            return []

        try:
            updates = await notifier.fetch_updates(limit=20)
        except Exception as e:
            logger.warning("GitHub agent: failed to fetch Telegram updates: %s", e)
            return []

        new_commands: list[OwnerCommand] = []
        for update in updates:
            if update.message_id in self.processed_message_ids:
                continue

            self.processed_message_ids.add(update.message_id)
            cmd = self.parse_command(
                update.text,
                message_id=update.message_id,
                timestamp=getattr(update, "timestamp", ""),
            )
            if cmd:
                new_commands.append(cmd)
                self.pending_commands.append(cmd)
                logger.info(
                    "GitHub agent: owner command parsed: %s %s#%d",
                    cmd.verb, cmd.repo, cmd.number,
                )

        # Prevent unbounded growth of processed IDs set
        if len(self.processed_message_ids) > self._max_processed_ids:
            sorted_ids = sorted(self.processed_message_ids)
            keep = sorted_ids[len(sorted_ids) // 2:]  # Keep newer half
            self.processed_message_ids = set(keep)

        return new_commands

    def pop_commands(self) -> list[OwnerCommand]:
        """Return and clear all pending commands."""
        commands = list(self.pending_commands)
        self.pending_commands.clear()
        return commands

    def format_for_planner(self) -> str:
        """
        Format pending commands as planner context.

        Returns an empty string if no pending commands,
        or a formatted block that the planner sees as highest-priority input.
        """
        if not self.pending_commands:
            return ""

        lines = ["PENDING OWNER COMMANDS (highest priority — execute these first):"]
        for cmd in self.pending_commands:
            line = f"  - {cmd.verb} {cmd.repo}#{cmd.number}"
            if cmd.args:
                line += f" ({cmd.args})"
            lines.append(line)

        return "\n".join(lines)
