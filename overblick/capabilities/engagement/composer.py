"""
ComposerCapability — wraps ResponseGenerator as a composable capability.

Generates LLM-powered responses (comments, replies, heartbeats) using
the SafeLLMPipeline for full security enforcement.
"""

import logging
from typing import Optional

from overblick.capabilities.engagement.response_gen import ResponseGenerator
from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.core.security.settings import raw_llm

logger = logging.getLogger(__name__)


class ComposerCapability(CapabilityBase):
    """
    Response composition capability.

    Wraps the ResponseGenerator module, providing comment/reply/heartbeat
    generation through the standard capability lifecycle. Uses SafeLLMPipeline
    for automatic security enforcement.
    """

    name = "composer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._generator: ResponseGenerator | None = None

    async def setup(self) -> None:
        # Prefer pipeline (secure)
        pipeline = self.ctx.llm_pipeline

        if not pipeline:
            logger.warning(
                "ComposerCapability: no LLM pipeline available for %s", self.ctx.identity_name
            )
            return

        self._generator = ResponseGenerator(
            llm_pipeline=pipeline,
        )
        logger.info("ComposerCapability initialized for %s", self.ctx.identity_name)

    async def compose_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        existing_comments: list[str] | None = None,
        extra_context: str = "",
    ) -> str | None:
        """Generate a comment response to a post."""
        if not self._generator:
            return None

        system_prompt = self.ctx.config.get("system_prompt", f"You are {self.ctx.identity_name}.")
        temperature = self.ctx.config.get("temperature", 0.7)
        max_tokens = self.ctx.config.get("max_tokens", 500)

        # Build prompt from template
        format_vars = {
            "title": post_title,
            "content": post_content,
            "post_content": post_content,
            "agent_name": agent_name,
            "author": agent_name,
            "existing_comments": "",
        }
        if existing_comments:
            comments_text = "\n".join(f"- {c}" for c in existing_comments[:5])
            format_vars["existing_comments"] = comments_text

        prompt = prompt_template.format(**format_vars)

        context_items = []
        if extra_context:
            context_items.append(extra_context)

        return await self._generator.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="composer_comment",
            priority="low",
        )

    async def compose_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
    ) -> str | None:
        """Generate a reply to a comment."""
        if not self._generator:
            return None

        system_prompt = self.ctx.config.get("system_prompt", f"You are {self.ctx.identity_name}.")
        temperature = self.ctx.config.get("temperature", 0.7)
        max_tokens = self.ctx.config.get("max_tokens", 500)

        format_vars = {
            "title": original_post_title,
            "post_title": original_post_title,
            "comment": comment_content,
            "comment_content": comment_content,
            "commenter_name": commenter_name,
            "commenter": commenter_name,
        }
        prompt = prompt_template.format(**format_vars)

        return await self._generator.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            audit_action="composer_reply",
            priority="low",
        )

    async def compose_heartbeat(
        self,
        prompt_template: str,
        topic_index: int = 0,
    ) -> tuple[str, str, str] | None:
        """Generate a heartbeat post."""
        if not self._generator:
            return None

        system_prompt = self.ctx.config.get("system_prompt", f"You are {self.ctx.identity_name}.")
        temperature = self.ctx.config.get("temperature", 0.8)
        max_tokens = self.ctx.config.get("max_tokens", 1000)

        format_vars = {"topic_index": topic_index}
        prompt = prompt_template.format(**format_vars)

        raw_content = await self._generator.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            audit_action="composer_heartbeat",
            priority="low",
        )

        if not raw_content:
            return None

        if raw_content.startswith("I'm not able to") or "caught my attention" in raw_content:
            # This is a deflection from SafeLLMPipeline
            return "Untitled Post", raw_content, "ai"

        return self._parse_post_output(raw_content)

    def _parse_post_output(self, text: str) -> tuple[str, str, str]:
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

    @property
    def inner(self) -> ResponseGenerator | None:
        """Access the underlying ResponseGenerator (for tests/migration)."""
        return self._generator
