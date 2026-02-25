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
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from .models import (
    Agent, Post, Comment, Conversation, DMRequest, FeedItem,
    Message, SearchResult, Submolt,
)
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


class SuspensionError(MoltbookError):
    """Raised when the account is suspended."""

    # Regex to extract ISO timestamp from "suspended until <datetime>" messages
    _UNTIL_PATTERN = re.compile(r"suspended until (\d{4}-\d{2}-\d{2}T[\d:.]+Z?)")

    def __init__(self, message: str, suspended_until: str = "", reason: str = ""):
        super().__init__(message)
        # Auto-parse suspended_until from message if not explicitly provided
        if not suspended_until:
            match = self._UNTIL_PATTERN.search(message)
            if match:
                suspended_until = match.group(1)
        self.suspended_until = suspended_until
        self.reason = reason

    @property
    def suspended_until_dt(self) -> Optional[datetime]:
        """Parse suspended_until as a datetime, or None if unparseable."""
        if not self.suspended_until:
            return None
        try:
            ts = self.suspended_until
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None


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

        # Account status tracking
        self._account_status = "unknown"  # unknown, active, suspended, auth_error
        self._status_detail = ""
        self._status_updated_at = ""

        logger.info("MoltbookClient initialized: %s (identity=%s)", base_url, identity_name)

    def get_account_status(self) -> dict:
        """Get current account status."""
        return {
            "status": self._account_status,
            "detail": self._status_detail,
            "updated_at": self._status_updated_at,
            "identity": self._identity_name,
        }

    def _update_account_status(self, status: str, detail: str = "") -> None:
        """Update account status tracking."""
        self._account_status = status
        self._status_detail = detail
        self._status_updated_at = datetime.now(timezone.utc).isoformat()

    async def _ensure_session(self) -> None:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"Overblick/1.0 ({self._identity_name})",
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
                logger.debug("API %s %s -> CACHE HIT", method, endpoint)
                return cached

        # Wait for rate limit
        if not await self._rate_limiter.acquire_request():
            raise RateLimitError("Rate limit exceeded")

        await self._proxy.wait_for_rate_limit()
        await self._proxy.check_rate_limit()

        url = f"{self.base_url}{endpoint}"

        # Log outgoing request
        logger.debug(
            "API REQUEST: %s %s | params=%s | json_keys=%s",
            method, endpoint, params,
            list(json.keys()) if isinstance(json, dict) else None,
        )

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
                    timeout=aiohttp.ClientTimeout(total=90),
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
                                solved = await self._challenge_handler.solve(
                                    error_data,
                                    original_endpoint=endpoint,
                                    original_payload=json,
                                )
                                if solved is not None:
                                    return solved
                                logger.error("Challenge solving FAILED for POST %s", endpoint)
                        except json_module.JSONDecodeError:
                            pass
                        except MoltbookError:
                            raise
                        except Exception as e:
                            logger.debug("Challenge detection error: %s", e)

                    # Permanent errors — check for suspension first
                    if response.status == 401:
                        try:
                            error_data = json_module.loads(raw_body)
                            error_msg = error_data.get("error", "Invalid API key")
                            hint = error_data.get("hint", "")
                            api_message = error_data.get("message", "")
                            # Detect suspension in 401 response
                            combined = f"{error_msg} {hint} {api_message}".lower()
                            if "suspended" in combined:
                                full_msg = f"{api_message} {error_msg} {hint}".strip()
                                exc = SuspensionError(full_msg, reason=hint or error_msg)
                                logger.error(
                                    "SUSPENSION (401): %s | until=%s | full_response=%s",
                                    hint or error_msg, exc.suspended_until or "UNKNOWN",
                                    raw_body,
                                )
                                self._update_account_status("suspended", hint or error_msg)
                                raise exc
                            self._update_account_status("auth_error", error_msg)
                            if hint:
                                error_msg = f"{error_msg} -- {hint}"
                        except SuspensionError:
                            raise
                        except Exception:
                            error_msg = f"Auth error: {raw_body[:500]}"
                            self._update_account_status("auth_error", error_msg)
                        raise AuthenticationError(error_msg)
                    if response.status == 403:
                        # Parse full error response
                        try:
                            error_data = json_module.loads(raw_body)
                            error_msg = error_data.get("error", raw_body)
                            api_message = error_data.get("message", "")
                            api_path = error_data.get("path", endpoint)
                            api_timestamp = error_data.get("timestamp", "")
                        except json_module.JSONDecodeError:
                            error_msg = raw_body
                            api_message = ""
                            api_path = endpoint
                            api_timestamp = ""

                        # Detect suspension
                        combined = f"{error_msg} {api_message}".lower()
                        if "suspend" in combined:
                            # Extract suspended_until from message
                            full_msg = f"{api_message} {error_msg}"
                            exc = SuspensionError(full_msg, reason=error_msg)
                            logger.error(
                                "SUSPENSION: %s | until=%s | path=%s | server_time=%s | full_response=%s",
                                error_msg, exc.suspended_until or "UNKNOWN",
                                api_path, api_timestamp, raw_body,
                            )
                            self._update_account_status("suspended", error_msg)
                            raise exc
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

                    # Permanent client error — endpoint does not exist
                    if response.status == 404:
                        raise MoltbookError(f"API 404: {raw_body}")

                    # Transient errors
                    if response.status in (500, 502, 503, 504):
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

                    # Success — log and update account status
                    raw_text = await response.text()
                    logger.debug(
                        "API RESPONSE: %s %s -> HTTP %d | %d bytes | headers=%s",
                        method, endpoint, response.status, len(raw_text),
                        {k: v for k, v in response.headers.items()
                         if k.lower() in ("content-type", "x-ratelimit-remaining",
                                          "x-challenge", "x-verification")
                         or k.lower().startswith("x-molt")},
                    )
                    self._update_account_status("active")
                    result = json_module.loads(raw_text)

                    # ResponseRouter inspection (LLM-based challenge detection on 2xx)
                    if self._response_router:
                        verdict = await self._response_router.inspect(result)
                        if verdict and verdict.is_challenge:
                            logger.warning("ResponseRouter detected CHALLENGE in %s %s", method, endpoint)
                            if method == "POST" and self._challenge_handler:
                                solved = await self._challenge_handler.solve(
                                    result,
                                    original_endpoint=endpoint,
                                    original_payload=json,
                                )
                                if solved is not None:
                                    return solved
                                raise MoltbookError("Failed to solve verification challenge")
                            else:
                                logger.warning(
                                    "Challenge detected in %s response but cannot auto-solve (non-POST)",
                                    method,
                                )

                    # Challenge handler fallback (direct detection on 2xx)
                    if self._challenge_handler:
                        if self._challenge_handler.detect(result, response.status):
                            logger.warning("Challenge detected in %s %s (HTTP %d)", method, endpoint, response.status)
                            if method == "POST":
                                solved = await self._challenge_handler.solve(
                                    result,
                                    original_endpoint=endpoint,
                                    original_payload=json,
                                )
                                if solved is not None:
                                    return solved
                                raise MoltbookError("Failed to solve verification challenge")
                            else:
                                logger.warning(
                                    "Challenge in %s response logged but not auto-solved",
                                    method,
                                )

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
        self, title: str, content: str, submolt: str = "ai",
    ) -> Post:
        """Create a new post."""
        if not await self._rate_limiter.acquire_post():
            wait_time = self._rate_limiter.time_until_post()
            raise RateLimitError(f"Cannot post yet. Wait {wait_time:.0f} seconds.")

        data = await self._request(
            "POST", "/posts",
            json={"title": title, "content": content, "submolt_name": submolt},
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

    # ── Agent Management ─────────────────────────────────────────────────

    async def update_profile(self, description: str) -> Agent:
        """Update this agent's profile description."""
        data = await self._request("PATCH", "/agents/me", json={"description": description})
        return Agent.from_dict(data)

    async def get_agent_profile(self, name: str) -> Agent:
        """Get an agent's profile by name."""
        data = await self._request("GET", "/agents/profile", params={"name": name})
        return Agent.from_dict(data)

    # ── Link Posts ────────────────────────────────────────────────────────

    async def create_link_post(
        self, title: str, url: str, submolt: str = "ai",
    ) -> Post:
        """Create a link post (URL instead of text content)."""
        if not await self._rate_limiter.acquire_post():
            wait_time = self._rate_limiter.time_until_post()
            raise RateLimitError(f"Cannot post yet. Wait {wait_time:.0f} seconds.")

        data = await self._request(
            "POST", "/posts",
            json={"title": title, "url": url, "submolt_name": submolt},
        )
        post_data = data.get("post", data)
        logger.info("Link post created in m/%s: %s", submolt, title[:50])
        return Post.from_dict(post_data)

    async def delete_post(self, post_id: str) -> bool:
        """Delete a post by ID."""
        await self._request("DELETE", f"/posts/{post_id}")
        logger.info("Post deleted: %s", post_id)
        return True

    # ── Submolts ──────────────────────────────────────────────────────────

    async def list_submolts(self) -> list[Submolt]:
        """List available submolts."""
        data = await self._request("GET", "/submolts")
        return [Submolt.from_dict(s) for s in data.get("submolts", [])]

    async def get_submolt(self, name: str) -> Submolt:
        """Get submolt details by name."""
        data = await self._request("GET", f"/submolts/{name}")
        return Submolt.from_dict(data)

    async def subscribe_submolt(self, name: str) -> bool:
        """Subscribe to a submolt."""
        await self._request("POST", f"/submolts/{name}/subscribe")
        logger.info("Subscribed to submolt: %s", name)
        return True

    async def unsubscribe_submolt(self, name: str) -> bool:
        """Unsubscribe from a submolt."""
        await self._request("DELETE", f"/submolts/{name}/subscribe")
        logger.info("Unsubscribed from submolt: %s", name)
        return True

    # ── Following ─────────────────────────────────────────────────────────

    async def follow_agent(self, name: str) -> bool:
        """Follow an agent by name."""
        await self._request("POST", f"/agents/{name}/follow")
        logger.info("Followed agent: %s", name)
        return True

    async def unfollow_agent(self, name: str) -> bool:
        """Unfollow an agent by name."""
        await self._request("DELETE", f"/agents/{name}/follow")
        logger.info("Unfollowed agent: %s", name)
        return True

    # ── Direct Messages ───────────────────────────────────────────────────

    async def check_dm_activity(self) -> dict:
        """Check for DM activity (unread count, pending requests)."""
        return await self._request("GET", "/dms/activity")

    async def send_dm_request(self, recipient_id: str, message: str) -> dict:
        """Send a DM request to another agent."""
        data = await self._request(
            "POST", "/dms/request",
            json={"recipient_id": recipient_id, "message": message},
        )
        logger.info("DM request sent to %s", recipient_id)
        return data

    async def list_dm_requests(self) -> list[DMRequest]:
        """List pending DM requests."""
        data = await self._request("GET", "/dms/requests")
        return [DMRequest.from_dict(r) for r in data.get("requests", [])]

    async def approve_dm_request(self, request_id: str) -> bool:
        """Approve a DM request."""
        await self._request("POST", f"/dms/requests/{request_id}/approve")
        logger.info("DM request approved: %s", request_id)
        return True

    async def list_conversations(self) -> list[Conversation]:
        """List DM conversations."""
        data = await self._request("GET", "/dms/conversations")
        return [Conversation.from_dict(c) for c in data.get("conversations", [])]

    async def send_dm(self, conversation_id: str, message: str) -> Message:
        """Send a message in a DM conversation."""
        data = await self._request(
            "POST", f"/dms/conversations/{conversation_id}",
            json={"message": message},
        )
        msg_data = data.get("message", data)
        return Message.from_dict(msg_data)

    # ── Identity Protocol ─────────────────────────────────────────────────

    async def generate_identity_token(self) -> str:
        """Generate an identity verification token."""
        data = await self._request("POST", "/agents/me/identity-token")
        return data.get("token", "")

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
