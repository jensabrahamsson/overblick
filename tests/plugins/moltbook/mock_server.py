"""
Mock Moltbook HTTP server for integration testing.

Implements the full Moltbook API surface using aiohttp so that MoltbookClient
can be exercised against real HTTP — not Python-level mocks.

Usage:
    async with MockMoltbookServer() as server:
        client = MoltbookClient(base_url=server.base_url, ...)
        ...
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aiohttp import web


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

@dataclass
class MockMoltbookState:
    """All mutable server state for one test run."""

    posts: dict[str, dict] = field(default_factory=dict)       # post_id → post dict
    comments: dict[str, list[dict]] = field(default_factory=dict)  # post_id → [comment dicts]
    agents: dict[str, dict] = field(default_factory=dict)      # agent_id → agent dict
    votes: set[str] = field(default_factory=set)               # "post:{id}:{agent}" / "comment:{id}:{agent}"
    conversations: dict[str, dict] = field(default_factory=dict)   # conv_id → conversation dict
    dm_requests: list[dict] = field(default_factory=list)
    subscriptions: set[str] = field(default_factory=set)       # "agent_id:submolt_name"
    followers: dict[str, set[str]] = field(default_factory=dict)   # agent_name → {follower_ids}
    _counter: int = 0

    # Scenario controls
    challenge_on_next_post: bool = False  # inject challenge into next POST /posts
    suspended: bool = False               # return 401 "suspended" on all requests
    rate_limited: bool = False            # return 429 on all requests
    auth_error: bool = False              # return 401 without "suspended" keyword
    challenge_type: str = "math"

    def __post_init__(self) -> None:
        # Pre-populate the default agent that the test client acts as
        self.agents["agent-1"] = {
            "id": "agent-1",
            "name": "test-agent",
            "description": "Test agent for integration tests",
            "karma": 0,
            "verified": False,
            "is_claimed": False,
            "follower_count": 0,
        }

    def next_id(self) -> str:
        self._counter += 1
        return str(self._counter)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_dict(state: MockMoltbookState, post_id: str) -> dict:
    """Return a full post dict including its comments."""
    post = dict(state.posts[post_id])
    post["comments"] = list(state.comments.get(post_id, []))
    return post


def _maybe_inject_challenge(state: MockMoltbookState, result: dict) -> dict:
    """Optionally embed a verification challenge in result (POST /posts)."""
    if state.challenge_on_next_post:
        state.challenge_on_next_post = False
        result["verification_required"] = True
        result["verification"] = {
            "code": "test-code-123",
            "challenge": "What is 2 + 2?",
            "instructions": "Answer the math question",
        }
    return result


# ---------------------------------------------------------------------------
# Scenario middleware
# ---------------------------------------------------------------------------

@web.middleware
async def scenario_middleware(request: web.Request, handler) -> web.Response:
    """Short-circuit requests based on global scenario flags."""
    state: MockMoltbookState = request.app["state"]

    if state.suspended:
        return web.json_response(
            {"error": "Account suspended until 2999-01-01", "message": "suspended", "hint": ""},
            status=401,
        )

    if state.auth_error:
        return web.json_response(
            {"error": "Invalid API key"},
            status=401,
        )

    if state.rate_limited:
        return web.json_response(
            {"error": "Rate limit exceeded"},
            status=429,
            headers={"Retry-After": "0"},
        )

    return await handler(request)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

routes = web.RouteTableDef()

# ── Agents ────────────────────────────────────────────────────────────────

@routes.post("/api/v1/agents/register")
async def register_agent(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    body = await request.json()
    agent_id = state.next_id()
    agent = {
        "id": agent_id,
        "name": body.get("name", ""),
        "description": body.get("description", ""),
        "karma": 0,
        "verified": False,
        "is_claimed": False,
        "follower_count": 0,
    }
    state.agents[agent_id] = agent
    return web.json_response({"agent_id": agent_id, "token": f"tok-{agent_id}"})


@routes.get("/api/v1/agents/me")
async def get_self(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    return web.json_response(dict(state.agents["agent-1"]))


@routes.patch("/api/v1/agents/me")
async def update_self(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    body = await request.json()
    if "description" in body:
        state.agents["agent-1"]["description"] = body["description"]
    return web.json_response(dict(state.agents["agent-1"]))


@routes.get("/api/v1/agents/profile")
async def get_agent_profile(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    name = request.rel_url.query.get("name", "")
    for agent in state.agents.values():
        if agent["name"] == name:
            return web.json_response(dict(agent))
    return web.json_response({"error": "Agent not found"}, status=404)


@routes.post("/api/v1/agents/{name}/follow")
async def follow_agent(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    name = request.match_info["name"]
    followers = state.followers.setdefault(name, set())
    followers.add("agent-1")
    return web.json_response({"status": "following"})


@routes.delete("/api/v1/agents/{name}/follow")
async def unfollow_agent(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    name = request.match_info["name"]
    state.followers.get(name, set()).discard("agent-1")
    return web.json_response({"status": "unfollowed"})


@routes.post("/api/v1/agents/me/identity-token")
async def identity_token(request: web.Request) -> web.Response:
    return web.json_response({"token": "test-identity-token"})


# ── Posts ─────────────────────────────────────────────────────────────────

@routes.get("/api/v1/posts")
async def list_posts(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    limit = int(request.rel_url.query.get("limit", 20))
    submolt_filter = request.rel_url.query.get("submolt")

    all_posts = list(state.posts.values())
    if submolt_filter:
        all_posts = [p for p in all_posts if p.get("submolt") == submolt_filter]

    # Sort by id descending (newest first)
    all_posts = sorted(all_posts, key=lambda p: int(p["id"]), reverse=True)
    page = [dict(p) for p in all_posts[:limit]]

    return web.json_response({"posts": page, "total": len(all_posts)})


@routes.post("/api/v1/posts")
async def create_post(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    body = await request.json()
    post_id = state.next_id()
    post = {
        "id": post_id,
        "agent_id": "agent-1",
        "agent_name": "test-agent",
        "title": body.get("title", ""),
        "content": body.get("content", ""),
        "url": body.get("url", ""),
        "submolt": body.get("submolt_name", body.get("submolt", "ai")),
        "upvotes": 0,
        "downvotes": 0,
        "comment_count": 0,
    }
    state.posts[post_id] = post
    state.comments[post_id] = []
    result = {"post": dict(post)}
    result = _maybe_inject_challenge(state, result)
    return web.json_response(result)


@routes.get("/api/v1/posts/{post_id}")
async def get_post(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    if post_id not in state.posts:
        return web.json_response({"error": "Post not found"}, status=404)
    return web.json_response({"post": _post_dict(state, post_id)})


@routes.delete("/api/v1/posts/{post_id}")
async def delete_post(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    state.posts.pop(post_id, None)
    state.comments.pop(post_id, None)
    return web.json_response({"status": "deleted"})


@routes.post("/api/v1/posts/{post_id}/upvote")
async def upvote_post(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    if post_id in state.posts:
        state.posts[post_id]["upvotes"] += 1
    return web.json_response({"status": "ok"})


@routes.post("/api/v1/posts/{post_id}/downvote")
async def downvote_post(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    if post_id in state.posts:
        state.posts[post_id]["downvotes"] += 1
    return web.json_response({"status": "ok"})


# ── Comments ──────────────────────────────────────────────────────────────

@routes.post("/api/v1/posts/{post_id}/comments")
async def create_comment(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    if post_id not in state.posts:
        return web.json_response({"error": "Post not found"}, status=404)

    body = await request.json()
    comment_id = state.next_id()
    comment = {
        "id": comment_id,
        "post_id": post_id,
        "agent_id": "agent-1",
        "agent_name": "test-agent",
        "content": body.get("content", ""),
        "upvotes": 0,
        "parent_id": body.get("parent_id"),
    }
    state.comments.setdefault(post_id, []).append(comment)
    state.posts[post_id]["comment_count"] = len(state.comments[post_id])
    return web.json_response({"comment": dict(comment)})


@routes.get("/api/v1/posts/{post_id}/comments")
async def list_comments(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    post_id = request.match_info["post_id"]
    if post_id not in state.posts:
        return web.json_response({"error": "Post not found"}, status=404)
    comments = [dict(c) for c in state.comments.get(post_id, [])]
    return web.json_response({"comments": comments})


@routes.post("/api/v1/comments/{comment_id}/upvote")
async def upvote_comment(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    comment_id = request.match_info["comment_id"]
    for comments in state.comments.values():
        for c in comments:
            if c["id"] == comment_id:
                c["upvotes"] += 1
                return web.json_response({"status": "ok"})
    return web.json_response({"status": "ok"})


# ── Feed ──────────────────────────────────────────────────────────────────

@routes.get("/api/v1/feed")
async def get_feed(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    limit = int(request.rel_url.query.get("limit", 20))
    all_posts = sorted(state.posts.values(), key=lambda p: int(p["id"]), reverse=True)
    items = [
        {
            "post": dict(p),
            "relevance_score": 0.9,
            "recommended_action": "view",
            "reason": "recent post",
        }
        for p in all_posts[:limit]
    ]
    return web.json_response({"items": items})


# ── Search ────────────────────────────────────────────────────────────────

@routes.get("/api/v1/search")
async def search(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    query = request.rel_url.query.get("q", "").lower()
    limit = int(request.rel_url.query.get("limit", 20))
    results = [
        dict(p) for p in state.posts.values()
        if query in p.get("title", "").lower() or query in p.get("content", "").lower()
    ]
    return web.json_response({
        "posts": results[:limit],
        "total_count": len(results),
        "page": 1,
        "has_more": len(results) > limit,
    })


# ── Submolts ──────────────────────────────────────────────────────────────

_DEFAULT_SUBMOLTS = [
    {"name": "ai", "display_name": "AI", "description": "Artificial intelligence", "subscriber_count": 1000},
    {"name": "general", "display_name": "General", "description": "General discussion", "subscriber_count": 5000},
    {"name": "crypto", "display_name": "Crypto", "description": "Cryptocurrency", "subscriber_count": 800},
]


@routes.get("/api/v1/submolts")
async def list_submolts(request: web.Request) -> web.Response:
    return web.json_response({"submolts": list(_DEFAULT_SUBMOLTS)})


@routes.get("/api/v1/submolts/{name}")
async def get_submolt(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    for s in _DEFAULT_SUBMOLTS:
        if s["name"] == name:
            return web.json_response(dict(s))
    return web.json_response({"error": "Submolt not found"}, status=404)


@routes.post("/api/v1/submolts/{name}/subscribe")
async def subscribe_submolt(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    name = request.match_info["name"]
    state.subscriptions.add(f"agent-1:{name}")
    return web.json_response({"status": "subscribed"})


@routes.delete("/api/v1/submolts/{name}/subscribe")
async def unsubscribe_submolt(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    name = request.match_info["name"]
    state.subscriptions.discard(f"agent-1:{name}")
    return web.json_response({"status": "unsubscribed"})


# ── Direct Messages ───────────────────────────────────────────────────────

@routes.get("/api/v1/dms/activity")
async def dm_activity(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    pending = [r for r in state.dm_requests if r.get("status") == "pending"]
    return web.json_response({"unread_count": len(pending)})


@routes.post("/api/v1/dms/request")
async def send_dm_request(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    body = await request.json()
    request_id = state.next_id()
    dm_request = {
        "id": request_id,
        "sender_id": "agent-1",
        "sender_name": "test-agent",
        "recipient_id": body.get("recipient_id", ""),
        "message": body.get("message", ""),
        "status": "pending",
    }
    state.dm_requests.append(dm_request)
    return web.json_response({"request_id": request_id, "status": "sent"})


@routes.get("/api/v1/dms/requests")
async def list_dm_requests(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    pending = [r for r in state.dm_requests if r.get("status") == "pending"]
    formatted = [
        {
            "id": r["id"],
            "sender_id": r["sender_id"],
            "sender_name": r["sender_name"],
            "message": r["message"],
        }
        for r in pending
    ]
    return web.json_response({"requests": formatted})


@routes.post("/api/v1/dms/requests/{request_id}/approve")
async def approve_dm_request(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    request_id = request.match_info["request_id"]

    dm_req = next((r for r in state.dm_requests if r["id"] == request_id), None)
    if dm_req is None:
        return web.json_response({"error": "Request not found"}, status=404)

    dm_req["status"] = "approved"
    conv_id = state.next_id()
    state.conversations[conv_id] = {
        "id": conv_id,
        "participant_id": dm_req["sender_id"],
        "participant_name": dm_req["sender_name"],
        "last_message": dm_req["message"],
        "messages": [],
    }
    return web.json_response({"conversation_id": conv_id, "status": "approved"})


@routes.get("/api/v1/dms/conversations")
async def list_conversations(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    convs = [
        {
            "id": c["id"],
            "participant_id": c["participant_id"],
            "participant_name": c["participant_name"],
            "last_message": c["last_message"],
        }
        for c in state.conversations.values()
    ]
    return web.json_response({"conversations": convs})


@routes.post("/api/v1/dms/conversations/{conv_id}")
async def send_dm(request: web.Request) -> web.Response:
    state: MockMoltbookState = request.app["state"]
    conv_id = request.match_info["conv_id"]
    if conv_id not in state.conversations:
        return web.json_response({"error": "Conversation not found"}, status=404)

    body = await request.json()
    msg_id = state.next_id()
    message = {
        "id": msg_id,
        "sender_id": "agent-1",
        "sender_name": "test-agent",
        "content": body.get("message", ""),
    }
    state.conversations[conv_id]["messages"].append(message)
    state.conversations[conv_id]["last_message"] = message["content"]
    return web.json_response({"message": dict(message)})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_mock_app(state: MockMoltbookState) -> web.Application:
    """Create the aiohttp application wired with all routes and middleware."""
    app = web.Application(middlewares=[scenario_middleware])
    app["state"] = state
    app.router.add_routes(routes)
    return app


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class MockMoltbookServer:
    """
    Async context manager that starts the mock server on a free OS-assigned port.

    Attributes:
        base_url: The base URL to pass to MoltbookClient (includes /api/v1 prefix).
        state: Direct access to in-memory state for scenario setup.
    """

    state: MockMoltbookState
    base_url: str

    async def __aenter__(self) -> "MockMoltbookServer":
        self.state = MockMoltbookState()
        self._app = create_mock_app(self.state)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        # Port 0 → OS picks a free port, avoiding any conflicts
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        port = self._runner.addresses[0][1]
        self.base_url = f"http://127.0.0.1:{port}/api/v1"
        return self

    async def __aexit__(self, *_) -> None:
        await self._runner.cleanup()
