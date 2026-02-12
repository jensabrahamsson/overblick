"""
Moltbook API client — unified for all identities.

Async HTTP client for interacting with the Moltbook.com API.
Includes rate limiting, retry logic, response_router integration,
and per-content challenge handling.

All API responses pass through the ResponseRouter for transparent
challenge detection and LLM-based solving.
"""

import asyncio
import json as json_module
import logging
from typing import Optional

import aiohttp

from .models import Agent, Post, Comment, FeedItem, SearchResult
from .rate_limiter import MoltbookRateLimiter
from .request_proxy import MoltbookRequestProxy

logger = logging.getLogger(__name__)


class MoltbookError(Exception):
    """Base exception for Moltbook API errors."""
    pass


class RateLimitError(MoltbookError):
    """Raised when rate limited by Moltbook API."""
    pass


class AuthenticationError(MoltbookError):
    """Raised when authentication fails."""
    pass


class MoltbookClient:
    """
    Async client for Moltbook API.

    Features:
    - Rate limiting with token bucket
    - Exponential backoff retries
    - ResponseRouter integration (LLM inspection of all responses)
    - Per-content challenge handling
    - Request caching for GET requests
    """

    def __init__(
        self,
        base_url: str = "https://www.moltbook.com/api/v1",
        api_key: str = "",
        agent_id: str = "",
        identity_name: str = "",
        requests_per_minute: int = 100,
        post_interval_minutes: int = 30,
        comment_interval_seconds: int = 20,
        max_comments_per_day: int = 50,
        challenge_handler=None,
        response_router=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id
        self._identity_name = identity_name
        self._challenge_handler = challenge_handler
        self._response_router = response_router

        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = MoltbookRateLimiter(
            requests_per_minute=requests_per_minute,
            post_interval_minutes=post_interval_minutes,
            comment_interval_seconds=comment_interval_seconds,
            max_comments_per_day=max_comments_per_day,
        )

        self._proxy = MoltbookRequestProxy(
            max_requests_per_minute=10,
            cache_ttl_seconds=60,
            enable_cache=True,
        )

        logger.info("MoltbookClient initialized: %s (identity=%s)", base_url, identity_name)

    async def _ensure_session(self) -> None:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"Blick/1.0 ({self._identity_name})",
            }
            self._session = aiohttp.ClientSession(headers=headers)
            if self._challenge_handler:
                self._challenge_handler.set_session(self._session)

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        retry_count: int = 3,
    ) -> dict:
        """
        Make an authenticated API request with retry logic.

        All responses pass through the ResponseRouter for transparent
        challenge detection and LLM-based solving.
        """
        await self._ensure_session()

        # Check proxy cache for GET requests
        if method == "GET" and self._proxy._cache_enabled:
            cached = self._proxy._cache.get(method, endpoint, params)
            if cached is not None:
                return cached

        # Wait for rate limit
        if not await self._rate_limiter.acquire_request():
            raise RateLimitError("Rate limit exceeded")

        await self._proxy.wait_for_rate_limit()
        await self._proxy.check_rate_limit()

        url = f"{self.base_url}{endpoint}"

        MAX_RATE_LIMIT_RETRIES = 3
        MAX_RETRY_AFTER_SECONDS = 300
        rate_limit_retries = 0

        for attempt in range(retry_count):
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    # Forensic logging for all error responses
                    if response.status >= 400:
                        raw_body = await response.text()
                        challenge_headers = {
                            k: v for k, v in response.headers.items()
                            if k.lower().startswith("x-") or "challenge" in k.lower()
                            or "captcha" in k.lower() or "verify" in k.lower()
                        }
                        logger.warning(
                            "API %s %s -> HTTP %d | Headers: %s | Body: %.2000s",
                            method, endpoint, response.status,
                            challenge_headers or "(none)",
                            raw_body,
                        )

                        # Detect potential challenge in error response
                        body_lower = raw_body.lower()
                        if any(kw in body_lower for kw in (
                            "challenge", "captcha", "verification",
                            "ascii", "nonce", "moltcaptcha",
                        )):
                            logger.error(
                                "CHALLENGE DETECTED in HTTP %d! Body: %s | Headers: %s",
                                response.status, raw_body, dict(response.headers),
                            )

                    # Challenge interception for 4xx POST responses
                    if response.status >= 400 and method == "POST" and self._challenge_handler:
                        try:
                            error_data = json_module.loads(raw_body)
                            if self._challenge_handler.detect(error_data, response.status):
                                logger.warning("PER-CONTENT CHALLENGE in HTTP %d!", response.status)
                                solved = await self._challenge_handler.solve(error_data)
                                if solved is not None:
                                    return solved
                                logger.error("Challenge solving FAILED for POST %s", endpoint)
                        except json_module.JSONDecodeError:
                            pass
                        except MoltbookError:
                            raise
                        except Exception as e:
                            logger.debug("Challenge detection error: %s", e)

                    # Permanent errors
                    if response.status == 401:
                        try:
                            error_data = json_module.loads(raw_body)
                            error_msg = error_data.get("error", "Invalid API key")
                            hint = error_data.get("hint", "")
                            if hint:
                                error_msg = f"{error_msg} -- {hint}"
                        except Exception:
                            error_msg = f"Auth error: {raw_body[:500]}"
                        raise AuthenticationError(error_msg)
                    if response.status == 403:
                        raise MoltbookError(f"API 403 (Forbidden): {raw_body}")
                    if response.status == 405:
                        raise MoltbookError(f"API 405 (Method Not Allowed): {raw_body}")

                    # Rate limiting
                    if response.status == 429:
                        rate_limit_retries += 1
                        if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                            raise RateLimitError(f"Rate limited {rate_limit_retries} times")

                        raw_retry_after = response.headers.get("Retry-After", "60")
                        try:
                            retry_after = min(int(raw_retry_after), MAX_RETRY_AFTER_SECONDS)
                        except ValueError:
                            retry_after = 60

                        logger.warning(
                            "Rate limited (retry %d/%d), waiting %ds",
                            rate_limit_retries, MAX_RATE_LIMIT_RETRIES, retry_after,
                        )
                        self._proxy.handle_rate_limit_response(retry_after)
                        await asyncio.sleep(retry_after)
                        continue

                    # Transient errors
                    if response.status in (404, 500, 502, 503, 504):
                        if attempt < retry_count - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "API %d (attempt %d/%d), retrying in %ds",
                                response.status, attempt + 1, retry_count, backoff,
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise MoltbookError(f"API {response.status} after {retry_count} attempts: {raw_body}")

                    # Other 4xx
                    if response.status >= 400:
                        raise MoltbookError(f"API {response.status}: {raw_body}")

                    # Success — parse JSON
                    result = await response.json()

                    # ResponseRouter inspection (LLM-based challenge detection on 2xx)
                    if self._response_router and method == "POST":
                        verdict = await self._response_router.inspect(result)
                        if verdict and verdict.is_challenge:
                            logger.warning("ResponseRouter detected CHALLENGE in POST %s", endpoint)
                            if self._challenge_handler:
                                solved = await self._challenge_handler.solve(result)
                                if solved is not None:
                                    return solved
                                raise MoltbookError("Failed to solve verification challenge")

                    # Challenge handler fallback (direct detection on 2xx)
                    if method == "POST" and self._challenge_handler:
                        if self._challenge_handler.detect(result, response.status):
                            logger.warning("Challenge detected in POST %s (HTTP %d)", endpoint, response.status)
                            solved = await self._challenge_handler.solve(result)
                            if solved is not None:
                                return solved
                            raise MoltbookError("Failed to solve verification challenge")

                    # Cache GET responses
                    if method == "GET":
                        self._proxy.cache_response(method, endpoint, result, params)

                    return result

            except aiohttp.ClientError as e:
                logger.warning("Request failed (attempt %d): %s", attempt + 1, e)
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise MoltbookError(f"Request failed after {retry_count} attempts: {e}")

        raise MoltbookError("Request failed - max retries exceeded")

    # ── Agent Operations ──────────────────────────────────────────────────

    async def register_agent(self, name: str, description: str) -> dict:
        """Register a new agent on Moltbook."""
        data = await self._request("POST", "/agents/register", json={"name": name, "description": description})
        logger.info("Agent registered: %s", name)
        return data

    async def get_agent(self, agent_id: str) -> Agent:
        """Get agent profile by ID."""
        data = await self._request("GET", f"/agents/{agent_id}")
        return Agent.from_dict(data)

    async def get_self(self) -> Agent:
        """Get this agent's profile."""
        data = await self._request("GET", "/agents/me")
        return Agent.from_dict(data)

    async def get_my_posts(self, limit: int = 20, include_comments: bool = True) -> list[Post]:
        """Get this agent's own posts (workaround: filters from general feed)."""
        agent = await self.get_self()
        all_posts = await self.get_posts(limit=limit * 5, sort="recent")
        my_posts = [p for p in all_posts if p.agent_name == agent.name][:limit]

        if include_comments:
            for post in my_posts:
                if not post.comments:
                    try:
                        full_post = await self.get_post(post.id, include_comments=True)
                        post.comments = full_post.comments
                    except Exception as e:
                        logger.warning("Could not fetch comments for post %s: %s", post.id, e)

        return my_posts

    async def get_my_comments(self, limit: int = 50) -> list[Comment]:
        """Get this agent's own comments."""
        data = await self._request("GET", "/agents/me/comments", params={"limit": limit})
        return [Comment.from_dict(c) for c in data.get("comments", [])]

    # ── Feed Operations ───────────────────────────────────────────────────

    async def get_feed(self, limit: int = 20) -> list[FeedItem]:
        """Get personalized feed."""
        data = await self._request("GET", "/feed", params={"limit": limit})
        return [FeedItem.from_dict(item) for item in data.get("items", [])]

    async def get_posts(
        self, limit: int = 20, offset: int = 0, sort: str = "recent", submolt: str = None,
    ) -> list[Post]:
        """Get all posts (not personalized)."""
        params = {"limit": limit, "offset": offset, "sort": sort}
        if submolt:
            params["submolt"] = submolt
        data = await self._request("GET", "/posts", params=params)
        return [Post.from_dict(p) for p in data.get("posts", [])]

    async def search(self, query: str, limit: int = 20) -> SearchResult:
        """Search posts semantically."""
        data = await self._request("GET", "/search", params={"q": query, "limit": limit})
        return SearchResult.from_dict(data)

    # ── Post Operations ───────────────────────────────────────────────────

    async def create_post(
        self, title: str, content: str, submolt: str = "ai", tags: list[str] = None,
    ) -> Post:
        """Create a new post."""
        if not await self._rate_limiter.acquire_post():
            wait_time = self._rate_limiter.time_until_post()
            raise RateLimitError(f"Cannot post yet. Wait {wait_time:.0f} seconds.")

        data = await self._request(
            "POST", "/posts",
            json={"title": title, "content": content, "submolt": submolt, "tags": tags or []},
        )
        post_data = data.get("post", data)
        logger.info("Post created in m/%s: %s", submolt, title[:50])
        return Post.from_dict(post_data)

    async def get_post(self, post_id: str, include_comments: bool = True) -> Post:
        """Get a post by ID (3-tier fallback strategy)."""
        # Tier 1: Direct fetch
        try:
            data = await self._request(
                "GET", f"/posts/{post_id}",
                params={"include_comments": str(include_comments).lower()},
                retry_count=1,
            )
            post_data = data.get("post", data)
            if post_data.get("id") or post_data.get("title"):
                post = Post.from_dict(post_data)
                if include_comments and not post.comments:
                    comments_raw = post_data.get("comments", data.get("comments", []))
                    if comments_raw:
                        post.comments = [Comment.from_dict(c) for c in comments_raw]
                return post
        except RateLimitError:
            raise
        except MoltbookError:
            pass

        # Tier 2: Comments-only
        if include_comments:
            try:
                comments_data = await self._request(
                    "GET", f"/posts/{post_id}/comments",
                    params={"sort": "new"}, retry_count=1,
                )
                comments = []
                if isinstance(comments_data, list):
                    comments = [Comment.from_dict(c) for c in comments_data]
                elif isinstance(comments_data, dict) and "comments" in comments_data:
                    comments = [Comment.from_dict(c) for c in comments_data["comments"]]
                if comments:
                    return Post(id=post_id, agent_id="", agent_name="", title="", content="", comments=comments)
            except RateLimitError:
                raise
            except MoltbookError:
                pass

        # Tier 3: Feed search
        for submolt in [None, "ai", "general", "crypto", "introductions"]:
            try:
                posts = await self.get_posts(sort="new", limit=100, submolt=submolt)
            except RateLimitError:
                raise
            except MoltbookError:
                continue
            for post in posts:
                if post.id == post_id:
                    if include_comments and not post.comments:
                        try:
                            cd = await self._request("GET", f"/posts/{post_id}/comments", params={"sort": "new"})
                            if isinstance(cd, list):
                                post.comments = [Comment.from_dict(c) for c in cd]
                            elif isinstance(cd, dict) and "comments" in cd:
                                post.comments = [Comment.from_dict(c) for c in cd["comments"]]
                        except MoltbookError:
                            pass
                    return post

        raise MoltbookError(f"Post {post_id} not found")

    async def upvote_post(self, post_id: str) -> bool:
        """Upvote a post."""
        await self._request("POST", f"/posts/{post_id}/upvote")
        return True

    async def downvote_post(self, post_id: str) -> bool:
        """Downvote a post."""
        await self._request("POST", f"/posts/{post_id}/downvote")
        return True

    # ── Comment Operations ────────────────────────────────────────────────

    async def create_comment(
        self, post_id: str, content: str, parent_id: Optional[str] = None,
    ) -> Comment:
        """Create a comment on a post."""
        if not await self._rate_limiter.acquire_comment():
            status = self._rate_limiter.get_status()
            if status["daily_comments_remaining"] == 0:
                raise RateLimitError("Daily comment limit reached")
            wait_time = self._rate_limiter.time_until_comment()
            raise RateLimitError(f"Cannot comment yet. Wait {wait_time:.0f} seconds.")

        payload = {"content": content}
        if parent_id:
            payload["parent_id"] = parent_id

        data = await self._request("POST", f"/posts/{post_id}/comments", json=payload)
        comment_data = data.get("comment", data)
        comment = Comment.from_dict(comment_data)
        logger.info("Comment created on post %s", post_id)
        return comment

    async def upvote_comment(self, post_id: str, comment_id: str) -> bool:
        """Upvote a comment."""
        await self._request("POST", f"/comments/{comment_id}/upvote")
        return True

    async def downvote_comment(self, post_id: str, comment_id: str) -> bool:
        """Downvote a comment."""
        await self._request("POST", f"/comments/{comment_id}/downvote")
        return True

    # ── Utility ───────────────────────────────────────────────────────────

    def get_rate_limit_status(self) -> dict:
        """Get current rate limit status."""
        return self._rate_limiter.get_status()

    def get_proxy_stats(self) -> dict:
        """Get request proxy statistics."""
        return self._proxy.get_stats()

    async def health_check(self) -> bool:
        """Check if Moltbook API is accessible."""
        try:
            await self._request("GET", "/agents/me")
            return True
        except Exception as e:
            logger.warning("Moltbook health check failed: %s", e)
            return False

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        logger.debug("MoltbookClient closed")
