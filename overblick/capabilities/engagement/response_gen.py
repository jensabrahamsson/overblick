"""
Response generation capability — identity-aware content creation.

Uses SafeLLMPipeline to generate text in the identity's voice,
decorated with learned knowledge from the LearningStore.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from overblick.core.security.input_sanitizer import wrap_external_content

if TYPE_CHECKING:
    from overblick.core.llm.pipeline import SafeLLMPipeline

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """
    Identity-aware text generator.

    Orchestrates:
    1. Knowledge retrieval (LearningStore)
    2. Prompt assembly (Identity + Learning + Input)
    3. Safe LLM generation (SafeLLMPipeline)
    """

    def __init__(
        self,
        llm_pipeline: SafeLLMPipeline,
    ):
        """
        Initialize the generator.

        Args:
            llm_pipeline: Mandatory safe pipeline for all generation.
        """
        self._pipeline = llm_pipeline

    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        user_id: str = "system",
        temperature: float = 0.7,
        max_tokens: int = 500,
        audit_action: str = "generate_response",
        context_items: list[str] | None = None,
        priority: str = "low",
        complexity: str | None = None,
    ) -> str:
        """
        Generate a response in the identity's voice.

        Args:
            prompt: The user input or prompt to respond to.
            system_prompt: The identity's system prompt.
            user_id: ID for rate limiting and preflight.
            temperature: LLM temperature.
            max_tokens: Max tokens to generate.
            audit_action: Action name for audit log.
            context_items: Optional strings to inject as context (e.g. learnings).
            priority: Gateway queue priority.
            complexity: Backend routing complexity.

        Returns:
            The generated safe text, or a deflection if blocked.
        """
        # Build messages
        messages = [{"role": "system", "content": system_prompt}]

        # Inject context if provided
        if context_items:
            context_text = "\n\nContext and learnings:\n- " + "\n- ".join(context_items)
            messages[0]["content"] += context_text

        # Add user prompt (wrapped as external content)
        messages.append({"role": "user", "content": wrap_external_content(prompt)})

        # Call pipeline (Strictly safe)
        result = await self._pipeline.chat(
            messages=messages,
            user_id=user_id,
            temperature=temperature,
            max_tokens=max_tokens,
            audit_action=audit_action,
            priority=priority,
            complexity=complexity,
        )

        if result.blocked:
            logger.warning(
                "Response generation blocked at %s: %s",
                result.block_stage.value if result.block_stage else "unknown",
                result.block_reason,
            )
            return result.deflection or "I'm not able to respond to that right now."

        return result.content or ""
