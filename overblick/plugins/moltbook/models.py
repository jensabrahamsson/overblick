"""
Data models for Moltbook API.

Defines the structure of posts, comments, agents, and feed items.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator


class Agent(BaseModel):
    """Represents an AI agent on Moltbook."""
    id: str
    name: str
    description: str = ""
    owner: str = ""
    karma: int = 0
    verified: bool = False
    is_claimed: bool = False
    follower_count: int = 0
    created_at: Optional[datetime] = None
    avatar_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        """Create Agent from API response dict."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            owner=data.get("owner", ""),
            karma=data.get("karma", 0),
            verified=data.get("verified", False),
            is_claimed=data.get("is_claimed", False),
            follower_count=data.get("follower_count", 0),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            avatar_url=data.get("avatar_url"),
        )


class Comment(BaseModel):
    """Represents a comment on a Moltbook post."""
    id: str
    post_id: str
    agent_id: str
    agent_name: str
    content: str
    upvotes: int = 0
    created_at: Optional[datetime] = None
    parent_id: Optional[str] = None  # For nested comments

    @classmethod
    def from_dict(cls, data: dict) -> "Comment":
        """Create Comment from API response dict."""
        # Handle author structure (API returns author.id/author.name)
        # Use `or {}` because API may return explicit null for author
        author = data.get("author") or {}
        agent_id = data.get("agent_id") or author.get("id", "")
        agent_name = data.get("agent_name") or author.get("name", "")

        return cls(
            id=data.get("id", ""),
            post_id=data.get("post_id", ""),
            agent_id=agent_id,
            agent_name=agent_name,
            content=data.get("content", ""),
            upvotes=data.get("upvotes", 0),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            parent_id=data.get("parent_id"),
        )


class Post(BaseModel):
    """Represents a post on Moltbook."""
    id: str
    agent_id: str
    agent_name: str
    title: str
    content: str
    submolt: str = ""
    upvotes: int = 0
    downvotes: int = 0
    comment_count: int = 0
    created_at: Optional[datetime] = None
    comments: list[Comment] = []
    tags: list[str] = []

    @classmethod
    def from_dict(cls, data: dict) -> "Post":
        """Create Post from API response dict."""
        comments = [Comment.from_dict(c) for c in data.get("comments", [])]

        # Handle author structure (API returns author.id/author.name, not agent_id/agent_name)
        # Use `or {}` because API may return explicit null for author
        author = data.get("author") or {}
        agent_id = data.get("agent_id") or author.get("id", "")
        agent_name = data.get("agent_name") or author.get("name", "")

        return cls(
            id=data.get("id", ""),
            agent_id=agent_id,
            agent_name=agent_name,
            title=data.get("title", ""),
            content=data.get("content", ""),
            submolt=data.get("submolt", ""),
            upvotes=data.get("upvotes", 0),
            downvotes=data.get("downvotes", 0),
            comment_count=data.get("comment_count", 0),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            comments=comments,
            tags=data.get("tags", []),
        )


class FeedItem(BaseModel):
    """
    Represents an item in the personalized feed.

    Feed items include relevance scoring and engagement recommendations.
    """
    post: Post
    relevance_score: float = 0.0
    recommended_action: str = "view"  # view, comment, upvote, skip
    reason: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "FeedItem":
        """Create FeedItem from API response dict."""
        return cls(
            post=Post.from_dict(data.get("post", {})),
            relevance_score=data.get("relevance_score", 0.0),
            recommended_action=data.get("recommended_action", "view"),
            reason=data.get("reason", ""),
        )


class SearchResult(BaseModel):
    """Represents a search result from Moltbook."""
    posts: list[Post] = []
    total_count: int = 0
    page: int = 1
    has_more: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResult":
        """Create SearchResult from API response dict."""
        posts = [Post.from_dict(p) for p in data.get("posts", [])]
        return cls(
            posts=posts,
            total_count=data.get("total_count", len(posts)),
            page=data.get("page", 1),
            has_more=data.get("has_more", False),
        )
