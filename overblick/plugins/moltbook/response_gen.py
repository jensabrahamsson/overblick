"""
Moltbook-specific response generation.

Wraps the core ResponseGenerator capability with Moltbook-specific
prompt logic and parsing (e.g. heartbeat title/content/submolt).
"""

import logging
from typing import Any, Optional, Tuple

from overblick.capabilities.engagement.response_gen import ResponseGenerator as CoreResponseGenerator
from overblick.core.llm.pipeline import SafeLLMPipeline

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """
    Moltbook-specific response generator.
    
    Wraps CoreResponseGenerator to provide specialized methods for
    comments, replies, heartbeats, and DMs.
    """

    def __init__(self, llm_pipeline: SafeLLMPipeline):
        """Initialize with core generator."""
        self._core = CoreResponseGenerator(llm_pipeline=llm_pipeline)

    async def generate_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        system_prompt: str,
        existing_comments: list[str] | None = None,
        extra_context: str | None = None,
        extra_format_vars: dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
        priority: str = "low",
    ) -> Optional[str]:
        """Generate a comment on a Moltbook post."""
        format_vars = {
            "title": post_title,
            "content": post_content,
            "agent_name": agent_name,
        }
        if extra_format_vars:
            format_vars.update(extra_format_vars)

        prompt = prompt_template.format(**format_vars)
        
        context_items = []
        if extra_context:
            context_items.append(extra_context)
        if existing_comments:
            comments_text = "\n".join(f"- {c}" for c in existing_comments[:5])
            context_items.append(f"Recent comments on this post:\n{comments_text}")

        raw_content = await self._core.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="moltbook_comment",
            priority=priority,
        )

        if not raw_content or raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            return None

        return raw_content

    async def generate_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
        system_prompt: str,
        extra_context: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 500,
        priority: str = "low",
    ) -> Optional[str]:
        """Generate a reply to a comment."""
        prompt = prompt_template.format(
            title=original_post_title,
            comment=comment_content,
            agent_name=commenter_name,
        )
        
        context_items = [extra_context] if extra_context else None

        raw_content = await self._core.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="moltbook_reply",
            priority=priority,
        )

        if not raw_content or raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            return None

        return raw_content

    async def generate_heartbeat(
        self,
        prompt_template: str,
        system_prompt: str,
        topic_index: int = 0,
        topic_vars: dict[str, Any] | None = None,
        extra_context: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 1000,
        priority: str = "low",
    ) -> Optional[Tuple[str, str, str]]:
        """
        Generate a new heartbeat post.
        
        Returns:
            (title, content, submolt) or None on failure.
        """
        format_vars = {"topic_index": topic_index}
        if topic_vars:
            format_vars.update(topic_vars)

        prompt = prompt_template.format(**format_vars)
        context_items = [extra_context] if extra_context else None

        raw_content = await self._core.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="moltbook_heartbeat",
            priority=priority,
        )

        if not raw_content:
            return None

        if raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            # This is a deflection from SafeLLMPipeline
            return "Untitled Post", raw_content, "ai"

        return self._parse_post_output(raw_content)

    async def generate_dm_reply(
        self,
        sender_name: str,
        message: str,
        prompt_template: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 500,
        priority: str = "high",
    ) -> Optional[str]:
        """Generate a reply to a DM."""
        # Wrap sender and message for security
        safe_sender = f"<<<EXTERNAL_SENDER: {sender_name}>>>"
        safe_msg = f"<<<EXTERNAL_MESSAGE: {message}>>>"
        
        prompt = prompt_template.format(
            sender=safe_sender,
            message=safe_msg,
        )

        raw_content = await self._core.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            audit_action="moltbook_dm_reply",
            priority=priority,
        )

        if not raw_content or raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            return None

        return raw_content

    async def generate_dream_post(
        self,
        dream: dict[str, Any],
        prompt_template: str,
        system_prompt: str,
        extra_format_vars: dict[str, Any] | None = None,
        extra_context: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 1000,
    ) -> Optional[Tuple[str, str, str]]:
        """Generate a post based on a dream."""
        format_vars = {
            "dream_content": dream.get("content", ""),
            "dream_mood": dream.get("mood", ""),
            "dream_symbols": ", ".join(dream.get("symbols", [])),
        }
        if extra_format_vars:
            format_vars.update(extra_format_vars)

        try:
            prompt = prompt_template.format(**format_vars)
        except KeyError as e:
            logger.warning("Dream journal prompt template missing key: %s", e)
            return None

        context_items = [extra_context] if extra_context else None

        raw_content = await self._core.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="moltbook_dream_post",
        )

        if not raw_content:
            return None

        if raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            return "Untitled Post", raw_content, "ai"

        return self._parse_post_output(raw_content)

    def _parse_post_output(self, text: str) -> Tuple[str, str, str]:
        """Parse LLM output into (title, content, submolt)."""
        lines = text.strip().split("\n")
        title = "Untitled Post"
        submolt = "ai"
        content_lines = []

        for line in lines:
            if line.upper().startswith("TITLE:"):
                title = line[6:].strip()
            elif line.upper().startswith("SUBMOLT:"):
                submolt = line[8:].strip().lower()
            else:
                content_lines.append(line)

        content = "\n".join(content_lines).strip()
        # Fallback if title was just first line
        if title == "Untitled Post" and content_lines:
            title = content_lines[0][:50]
            if len(content_lines[0]) > 50:
                title += "..."

        return title, content, submolt
