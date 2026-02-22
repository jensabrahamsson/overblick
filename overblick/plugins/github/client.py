"""
Async GitHub REST API client.

Wraps aiohttp for authenticated access to the GitHub API.
Handles rate limiting, retries, and Bearer token auth.
"""

import asyncio
import base64
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors."""
    pass


class RateLimitError(GitHubAPIError):
    """Raised when GitHub rate limit is exhausted."""
    def __init__(self, message: str, reset_at: int = 0):
        super().__init__(message)
        self.reset_at = reset_at


class GitHubAPIClient:
    """
    Async client for the GitHub REST API.

    Uses a PAT (Personal Access Token) with public_repo scope
    for both reading public repos and posting comments.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str = "", base_url: str = ""):
        self._token = token
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_remaining: int = 5000
        self._rate_limit_reset: int = 0

    @property
    def rate_limit_remaining(self) -> int:
        return self._rate_limit_remaining

    async def _ensure_session(self) -> None:
        """Create HTTP session if needed."""
        if self._session is None or self._session.closed:
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "Overblick-GitHub-Plugin/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._session = aiohttp.ClientSession(headers=headers)

    def _update_rate_limit(self, headers: dict) -> None:
        """Extract rate limit info from response headers."""
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self._rate_limit_remaining = int(remaining)
            except (ValueError, TypeError):
                pass
        reset = headers.get("X-RateLimit-Reset")
        if reset is not None:
            try:
                self._rate_limit_reset = int(reset)
            except (ValueError, TypeError):
                pass

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        retry_count: int = 3,
    ) -> Any:
        """Make an authenticated API request with retry logic."""
        await self._ensure_session()
        url = f"{self._base_url}{endpoint}"

        for attempt in range(retry_count):
            try:
                async with self._session.request(
                    method, url, params=params, json=json,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    self._update_rate_limit(response.headers)

                    if response.status == 200 or response.status == 201:
                        return await response.json()

                    raw_body = await response.text()

                    # Rate limited
                    if response.status in (403, 429) and self._rate_limit_remaining == 0:
                        raise RateLimitError(
                            f"GitHub rate limit exhausted (resets at {self._rate_limit_reset})",
                            reset_at=self._rate_limit_reset,
                        )

                    # Not found
                    if response.status == 404:
                        raise GitHubAPIError(f"Not found: {endpoint}")

                    # Auth error
                    if response.status == 401:
                        raise GitHubAPIError(f"Authentication failed: {raw_body[:200]}")

                    # Transient errors — retry
                    if response.status in (500, 502, 503, 504):
                        if attempt < retry_count - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "GitHub API %d on %s %s (attempt %d/%d), retrying in %ds",
                                response.status, method, endpoint,
                                attempt + 1, retry_count, backoff,
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise GitHubAPIError(
                            f"GitHub API {response.status} after {retry_count} attempts: {raw_body[:200]}"
                        )

                    raise GitHubAPIError(f"GitHub API {response.status}: {raw_body[:200]}")

            except aiohttp.ClientError as e:
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise GitHubAPIError(f"Request failed after {retry_count} attempts: {e}")

        raise GitHubAPIError("Request failed — max retries exceeded")

    # ── Issues ────────────────────────────────────────────────────────────

    async def list_issues(
        self,
        repo: str,
        since: str = "",
        state: str = "open",
        labels: str = "",
        per_page: int = 30,
    ) -> list[dict]:
        """
        List issues for a repository.

        Args:
            repo: "owner/repo" format
            since: ISO 8601 timestamp — only issues updated after this
            state: "open", "closed", or "all"
            labels: Comma-separated label names
            per_page: Results per page (max 100)
        """
        params: dict[str, Any] = {
            "state": state,
            "per_page": min(per_page, 100),
            "sort": "updated",
            "direction": "desc",
        }
        if since:
            params["since"] = since
        if labels:
            params["labels"] = labels

        return await self._request("GET", f"/repos/{repo}/issues", params=params)

    async def list_issue_comments(
        self,
        repo: str,
        issue_number: int,
        since: str = "",
        per_page: int = 30,
    ) -> list[dict]:
        """List comments on an issue."""
        params: dict[str, Any] = {"per_page": min(per_page, 100)}
        if since:
            params["since"] = since

        return await self._request(
            "GET", f"/repos/{repo}/issues/{issue_number}/comments", params=params,
        )

    async def create_comment(
        self,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict:
        """Post a comment on an issue."""
        return await self._request(
            "POST", f"/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )

    # ── Repository Tree ───────────────────────────────────────────────────

    async def get_file_tree(
        self, repo: str, branch: str = "main",
    ) -> dict:
        """
        Get the full file tree for a repository branch.

        Uses the Git Trees API with recursive=1 for a single-call fetch
        of all paths. Returns the raw API response including tree sha.
        """
        return await self._request(
            "GET", f"/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )

    async def get_file_content(
        self, repo: str, path: str, ref: str = "",
    ) -> dict:
        """
        Get file content via the Contents API.

        Returns the raw API response (content is base64-encoded).
        """
        params = {}
        if ref:
            params["ref"] = ref
        return await self._request(
            "GET", f"/repos/{repo}/contents/{path}", params=params,
        )

    # ── Pull Requests ────────────────────────────────────────────────────

    async def list_pulls(
        self,
        repo: str,
        state: str = "open",
        per_page: int = 30,
    ) -> list[dict]:
        """
        List pull requests for a repository.

        Args:
            repo: "owner/repo" format
            state: "open", "closed", or "all"
            per_page: Results per page (max 100)
        """
        return await self._request(
            "GET", f"/repos/{repo}/pulls",
            params={"state": state, "per_page": min(per_page, 100)},
        )

    async def get_pull(self, repo: str, pull_number: int) -> dict:
        """Get a single pull request with merge status details."""
        return await self._request(
            "GET", f"/repos/{repo}/pulls/{pull_number}",
        )

    async def get_pull_diff(self, repo: str, pull_number: int) -> str:
        """Get the diff of a pull request as plain text."""
        await self._ensure_session()
        url = f"{self._base_url}/repos/{repo}/pulls/{pull_number}"
        headers = {"Accept": "application/vnd.github.diff"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with self._session.get(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            self._update_rate_limit(response.headers)
            if response.status == 200:
                return await response.text()
            raise GitHubAPIError(f"Failed to get PR diff: {response.status}")

    async def merge_pull(
        self,
        repo: str,
        pull_number: int,
        merge_method: str = "squash",
        commit_title: str = "",
    ) -> dict:
        """
        Merge a pull request.

        Args:
            repo: "owner/repo" format
            pull_number: PR number
            merge_method: "merge", "squash", or "rebase"
            commit_title: Custom merge commit title (optional)
        """
        body: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            body["commit_title"] = commit_title
        return await self._request(
            "PUT", f"/repos/{repo}/pulls/{pull_number}/merge",
            json=body,
        )

    async def create_pull_review(
        self,
        repo: str,
        pull_number: int,
        event: str = "APPROVE",
        body: str = "",
    ) -> dict:
        """
        Create a review on a pull request.

        Args:
            repo: "owner/repo" format
            pull_number: PR number
            event: "APPROVE", "REQUEST_CHANGES", or "COMMENT"
            body: Review comment body
        """
        payload: dict[str, str] = {"event": event}
        if body:
            payload["body"] = body
        return await self._request(
            "POST", f"/repos/{repo}/pulls/{pull_number}/reviews",
            json=payload,
        )

    async def list_pull_reviews(
        self, repo: str, pull_number: int,
    ) -> list[dict]:
        """List reviews on a pull request."""
        return await self._request(
            "GET", f"/repos/{repo}/pulls/{pull_number}/reviews",
        )

    # ── CI / Check Runs ──────────────────────────────────────────────────

    async def get_check_runs(self, repo: str, ref: str) -> dict:
        """
        Get check runs for a git reference (SHA, branch, tag).

        Returns the raw API response with check_runs array.
        """
        return await self._request(
            "GET", f"/repos/{repo}/commits/{ref}/check-runs",
        )

    async def get_combined_status(self, repo: str, ref: str) -> dict:
        """
        Get the combined commit status for a reference.

        Returns state: "success", "failure", "pending", or "error".
        """
        return await self._request(
            "GET", f"/repos/{repo}/commits/{ref}/status",
        )

    # ── Rate Limit ────────────────────────────────────────────────────────

    async def get_rate_limit(self) -> dict:
        """Get current rate limit status."""
        return await self._request("GET", "/rate_limit")

    # ── Utility ───────────────────────────────────────────────────────────

    @staticmethod
    def decode_content(content_b64: str) -> str:
        """Decode base64-encoded file content from the Contents API."""
        try:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            return ""

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
