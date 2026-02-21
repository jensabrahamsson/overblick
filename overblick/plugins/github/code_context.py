"""
Code context builder for the GitHub monitoring plugin.

Two-phase approach to code understanding without cloning:
1. File tree cache — fetches full repo tree via Trees API (one call)
2. Targeted file fetch — LLM selects relevant files, fetched via Contents API

Sha-based caching ensures unchanged files are never re-fetched.
"""

import fnmatch
import json
import logging
import time
from typing import Optional

from overblick.plugins.github.client import GitHubAPIClient
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import CachedFile, CodeContext, FileTreeEntry
from overblick.plugins.github.prompts import file_selector_prompt

logger = logging.getLogger(__name__)

# Limits
DEFAULT_MAX_FILES = 8
DEFAULT_MAX_FILE_SIZE = 50_000  # 50KB
DEFAULT_MAX_CONTEXT_CHARS = 48_000  # ~12K tokens
DEFAULT_TREE_REFRESH_MINUTES = 60
DEFAULT_INCLUDE_PATTERNS = ["*.py", "*.yaml", "*.yml", "*.md", "*.toml", "*.json", "*.js", "*.ts"]
DEFAULT_EXCLUDE_PATTERNS = ["*.lock", "node_modules/*", "__pycache__/*", ".git/*", "*.min.js", "*.min.css"]


class CodeContextBuilder:
    """
    Build code context for answering GitHub questions.

    Uses the LLM pipeline (low complexity) for file selection,
    and the GitHub API client for fetching file tree and contents.
    """

    def __init__(
        self,
        client: GitHubAPIClient,
        db: GitHubDB,
        llm_pipeline=None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        tree_refresh_minutes: int = DEFAULT_TREE_REFRESH_MINUTES,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ):
        self._client = client
        self._db = db
        self._llm_pipeline = llm_pipeline
        self._max_files = max_files
        self._max_file_size = max_file_size
        self._max_context_chars = max_context_chars
        self._tree_refresh_minutes = tree_refresh_minutes
        self._include_patterns = include_patterns or DEFAULT_INCLUDE_PATTERNS
        self._exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS

    async def refresh_tree(self, repo: str, branch: str = "main") -> bool:
        """
        Refresh the file tree cache for a repository.

        Compares root sha to detect changes — if unchanged, skips update.

        Returns:
            True if tree was refreshed, False if skipped (unchanged or too recent)
        """
        # Check if refresh is needed
        meta = await self._db.get_tree_meta(repo)
        if meta:
            from datetime import datetime, timezone
            try:
                last_refreshed = datetime.fromisoformat(
                    meta["last_refreshed"].replace("Z", "+00:00")
                )
                age_minutes = (datetime.now(timezone.utc) - last_refreshed).total_seconds() / 60
                if age_minutes < self._tree_refresh_minutes:
                    logger.debug("GitHub: tree cache for %s still fresh (%.1fm old)", repo, age_minutes)
                    return False
            except (ValueError, TypeError):
                pass

        try:
            tree_data = await self._client.get_file_tree(repo, branch)
        except Exception as e:
            logger.warning("GitHub: failed to fetch tree for %s: %s", repo, e)
            return False

        root_sha = tree_data.get("sha", "")

        # Check if tree actually changed
        if meta and meta.get("root_sha") == root_sha and root_sha:
            await self._db.update_tree_meta(repo, root_sha)
            logger.debug("GitHub: tree unchanged for %s (sha=%s)", repo, root_sha[:8])
            return False

        # Clear old entries and rebuild
        await self._db.clear_tree(repo)

        tree_items = tree_data.get("tree", [])
        for item in tree_items:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            if not self._should_include(path):
                continue
            entry = FileTreeEntry(
                path=path,
                sha=item.get("sha", ""),
                size=item.get("size", 0),
            )
            await self._db.upsert_tree_entry(repo, entry)

        await self._db.update_tree_meta(repo, root_sha)
        logger.info("GitHub: refreshed tree for %s (%d files)", repo, len(tree_items))
        return True

    def _should_include(self, path: str) -> bool:
        """Check if a file path matches include/exclude patterns."""
        # Check excludes first
        for pattern in self._exclude_patterns:
            if fnmatch.fnmatch(path, pattern):
                return False

        # Check includes
        for pattern in self._include_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True

        return False

    async def select_files(self, repo: str, question: str) -> list[str]:
        """
        Use LLM to select relevant files from the tree for a question.

        Args:
            repo: Repository in "owner/repo" format
            question: The question to find relevant files for

        Returns:
            List of file paths selected by the LLM
        """
        if not self._llm_pipeline:
            logger.debug("GitHub: no LLM pipeline for file selection")
            return []

        paths = await self._db.get_tree_paths(repo)
        if not paths:
            logger.debug("GitHub: no cached tree for %s", repo)
            return []

        # Build compact tree representation
        tree_text = "\n".join(paths)

        messages = file_selector_prompt(
            file_tree=tree_text,
            question=question,
            max_files=self._max_files,
        )

        try:
            result = await self._llm_pipeline.chat(
                messages=messages,
                audit_action="github_file_selection",
                skip_preflight=True,
                skip_output_safety=True,
                complexity="low",
            )
            if not result or result.blocked or not result.content:
                return []

            selected = self._parse_file_list(result.content.strip())

            # Filter to paths that actually exist in tree
            valid = [p for p in selected if p in paths]
            if len(valid) != len(selected):
                logger.debug(
                    "GitHub: LLM selected %d files, %d valid",
                    len(selected), len(valid),
                )

            return valid[:self._max_files]

        except Exception as e:
            logger.warning("GitHub: file selection failed: %s", e)
            return []

    @staticmethod
    def _parse_file_list(raw: str) -> list[str]:
        """Parse LLM output as a JSON array of file paths."""
        # Try direct JSON parse
        try:
            result = json.loads(raw)
            if isinstance(result, list):
                return [str(p) for p in result if isinstance(p, str)]
        except json.JSONDecodeError:
            pass

        # Try to find JSON array in response
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                result = json.loads(raw[start:end])
                if isinstance(result, list):
                    return [str(p) for p in result if isinstance(p, str)]
            except json.JSONDecodeError:
                pass

        return []

    async def fetch_files(self, repo: str, paths: list[str], ref: str = "") -> list[CachedFile]:
        """
        Fetch file contents, using sha-based caching.

        Files with unchanged sha are served from cache.
        """
        files: list[CachedFile] = []
        total_size = 0

        for path in paths:
            if total_size >= self._max_context_chars:
                logger.debug("GitHub: context size limit reached (%d chars)", total_size)
                break

            # Check sha from tree cache
            sha = await self._db.get_file_sha(repo, path)
            if sha:
                cached = await self._db.get_cached_file(repo, sha)
                if cached:
                    files.append(cached)
                    total_size += len(cached.content)
                    continue

            # Fetch from API
            try:
                data = await self._client.get_file_content(repo, path, ref=ref)
            except Exception as e:
                logger.debug("GitHub: failed to fetch %s/%s: %s", repo, path, e)
                continue

            # Decode content
            content_b64 = data.get("content", "")
            content = GitHubAPIClient.decode_content(content_b64)
            file_sha = data.get("sha", sha or "")
            file_size = len(content)

            # Skip oversized files
            if file_size > self._max_file_size:
                logger.debug("GitHub: skipping oversized file %s (%d bytes)", path, file_size)
                continue

            # Cache it
            if file_sha:
                await self._db.cache_file(repo, path, file_sha, content)

            cached_file = CachedFile(
                repo=repo,
                path=path,
                sha=file_sha,
                content=content,
            )
            files.append(cached_file)
            total_size += file_size

        return files

    async def build_context(self, repo: str, question: str, branch: str = "main") -> CodeContext:
        """
        Build complete code context for answering a question.

        Orchestrates: refresh tree -> select files -> fetch -> assemble.
        """
        # Refresh tree if stale
        await self.refresh_tree(repo, branch)

        # LLM selects relevant files
        selected_paths = await self.select_files(repo, question)
        if not selected_paths:
            return CodeContext(repo=repo, question=question)

        # Fetch file contents
        files = await self.fetch_files(repo, selected_paths)
        total_size = sum(len(f.content) for f in files)

        logger.info(
            "GitHub: built context for %s (%d files, %d chars)",
            repo, len(files), total_size,
        )

        return CodeContext(
            repo=repo,
            question=question,
            files=files,
            total_size=total_size,
        )

    @staticmethod
    def format_context(context: CodeContext) -> str:
        """Format code context as a string for LLM consumption."""
        if not context.files:
            return "(no code context available)"

        parts = []
        for f in context.files:
            parts.append(f"--- {f.path} ---\n{f.content}")

        return "\n\n".join(parts)
