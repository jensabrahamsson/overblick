"""
ComposerCapability — wraps ResponseGenerator as a composable capability.

Generates LLM-powered responses (comments, replies, heartbeats) using
the SafeLLMPipeline for full security enforcement.
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext
from overblick.capabilities.engagement.response_gen import ResponseGenerator

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
        self._generator: Optional[ResponseGenerator] = None

    async def setup(self) -> None:
        system_prompt = self.ctx.config.get("system_prompt", f"You are {self.ctx.identity_name}.")
        temperature = self.ctx.config.get("temperature", 0.7)
        max_tokens = self.ctx.config.get("max_tokens", 2000)

        # Prefer pipeline (secure), fall back to raw client
        pipeline = self.ctx.llm_pipeline
        llm_client = self.ctx.llm_client if not pipeline else None

        if not pipeline and not llm_client:
            logger.warning("ComposerCapability: no LLM available for %s", self.ctx.identity_name)
            return

        self._generator = ResponseGenerator(
            llm_pipeline=pipeline,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            llm_client=llm_client,
        )
        logger.info("ComposerCapability initialized for %s", self.ctx.identity_name)

    async def compose_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        existing_comments: Optional[list[str]] = None,
        extra_context: str = "",
    ) -> Optional[str]:
        """Generate a comment response to a post."""
        if not self._generator:
            return None
        return await self._generator.generate_comment(
            post_title=post_title,
            post_content=post_content,
            agent_name=agent_name,
            prompt_template=prompt_template,
            existing_comments=existing_comments,
            extra_context=extra_context,
        )

    async def compose_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
    ) -> Optional[str]:
        """Generate a reply to a comment."""
        if not self._generator:
            return None
        return await self._generator.generate_reply(
            original_post_title=original_post_title,
            comment_content=comment_content,
            commenter_name=commenter_name,
            prompt_template=prompt_template,
        )

    async def compose_heartbeat(
        self,
        prompt_template: str,
        topic_index: int = 0,
    ) -> Optional[tuple[str, str, str]]:
        """Generate a heartbeat post."""
        if not self._generator:
            return None
        return await self._generator.generate_heartbeat(
            prompt_template=prompt_template,
            topic_index=topic_index,
        )

    @property
    def inner(self) -> Optional[ResponseGenerator]:
        """Access the underlying ResponseGenerator (for tests/migration)."""
        return self._generator
