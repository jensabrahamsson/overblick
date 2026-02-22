"""GitHub agent plugin — agentic repository caretaker."""

from overblick.plugins.github.plugin import GitHubAgentPlugin

# Backward-compatible alias
GitHubPlugin = GitHubAgentPlugin

__all__ = ["GitHubAgentPlugin", "GitHubPlugin"]
