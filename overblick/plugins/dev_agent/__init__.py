"""
Dev agent plugin — autonomous bug-fixing agent ("Smed").

Watches for bugs in GitHub issues and log files, uses opencode with
Devstral 2 (local LLM) to analyze and fix them, runs tests, and
creates PRs. Never commits to main directly.
"""
