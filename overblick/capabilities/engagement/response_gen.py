"""
Response generation capability — identity-aware content creation.

Uses SafeLLMPipeline to generate text in the identity's voice,
decorated with learned knowledge from the LearningStore.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
        llm_pipeline: SafeLLMPipeline | None = None,
        # Deprecated parameters (kept for backward compatibility)
        llm_client: Any | None = None,
        system_prompt: str | None = None,
        allow_raw_fallback: bool = False,
        temperature: float = 0.7,
        **kwargs,
    ):
        """
        Initialize the generator.

        Args:
            llm_pipeline: Safe pipeline for all generation (required for secure mode).
            llm_client: DEPRECATED - Raw LLM client (only used if llm_pipeline is None)
            system_prompt: DEPRECATED - System prompt (ignored, passed to generate())
            allow_raw_fallback: DEPRECATED - Allow raw fallback (ignored)
            temperature: DEPRECATED - Default temperature (ignored, passed to generate())
            **kwargs: Additional deprecated parameters (ignored)
        """
        if llm_client is not None:
            import warnings

            warnings.warn(
                "llm_client parameter is deprecated and ignored. Use llm_pipeline for secure LLM calls.",
                DeprecationWarning,
                stacklevel=2,
            )
        if system_prompt is not None:
            import warnings

            warnings.warn(
                "system_prompt parameter is deprecated and ignored. Pass system_prompt to generate() method.",
                DeprecationWarning,
                stacklevel=2,
            )
        if allow_raw_fallback:
            import warnings

            warnings.warn(
                "allow_raw_fallback is deprecated and ignored. Use llm_pipeline for secure LLM calls.",
                DeprecationWarning,
                stacklevel=2,
            )
        if kwargs:
            import warnings

            warnings.warn(
                f"Unknown parameters ignored: {list(kwargs.keys())}",
                DeprecationWarning,
                stacklevel=2,
            )

        if llm_pipeline is None and llm_client is None:
            raise ValueError(
                "Either llm_pipeline or llm_client must be provided. "
                "llm_pipeline is recommended for secure LLM calls."
            )

        if llm_pipeline is None and llm_client is not None:
            import warnings

            warnings.warn(
                "Using raw LLM client without SafeLLMPipeline bypasses security controls. "
                "This should only be used for testing and migration.",
                DeprecationWarning,
                stacklevel=2,
            )

        self._pipeline = llm_pipeline
        # Store deprecated parameters for backward compatibility
        self._deprecated_system_prompt = system_prompt
        self._deprecated_temperature = temperature
        # Backward compatibility attributes for tests
        self._llm = llm_client
        self._system_prompt = system_prompt

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
        skip_preflight: bool = False,
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
            skip_preflight: Skip preflight checks for system‑initiated content.

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

        # Call pipeline or raw client
        if self._pipeline:
            result = await self._pipeline._chat_with_overrides(
                messages=messages,
                user_id=user_id,
                temperature=temperature,
                max_tokens=max_tokens,
                audit_action=audit_action,
                priority=priority,
                complexity=complexity,
                skip_preflight=skip_preflight,
            )

            if result.blocked:
                logger.warning(
                    "Response generation blocked at %s: %s",
                    result.block_stage.value if result.block_stage else "unknown",
                    result.block_reason,
                )
                return result.deflection or "I'm not able to respond to that right now."

            return result.content or ""
        else:
            # Raw client fallback (deprecated)
            import warnings

            warnings.warn(
                "Using raw LLM client without SafeLLMPipeline bypasses security controls.",
                DeprecationWarning,
                stacklevel=2,
            )
            if self._llm is None:
                logger.error("No LLM client available")
                return ""

            # Raw client expects messages in same format
            try:
                raw_result = await self._llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                logger.error("Raw LLM client error: %s", e)
                return ""

            if isinstance(raw_result, dict) and "content" in raw_result:
                return raw_result["content"] or ""
            elif hasattr(raw_result, "content"):
                return raw_result.content or ""
            else:
                logger.error("Unexpected LLM client result format: %s", type(raw_result))
                return ""

    # --------------------------------------------------------------------------
    # Deprecated methods for backward compatibility (DO NOT USE IN NEW CODE)
    # --------------------------------------------------------------------------

    async def generate_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        existing_comments: list[str] | None = None,
        extra_context: str | None = None,
        extra_format_vars: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int = 500,
        priority: str = "low",
    ) -> str | None:
        """DEPRECATED: Generate a comment on a post."""
        import warnings

        warnings.warn(
            "generate_comment() is deprecated. Use generate() or ComposerCapability.",
            DeprecationWarning,
            stacklevel=2,
        )

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

        if extra_format_vars:
            format_vars.update(extra_format_vars)

        prompt = prompt_template.format(**format_vars)
        context_items = [extra_context] if extra_context else None

        if self._deprecated_system_prompt is None:
            logger.warning("No system_prompt provided to ResponseGenerator constructor")
            return None

        result = await self.generate(
            prompt=prompt,
            system_prompt=self._deprecated_system_prompt,
            temperature=temperature or self._deprecated_temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="generate_comment",
            priority=priority,
            skip_preflight=False,
        )

        # Filter out deflections
        if result.startswith("I'm not able to") or "caught my attention" in result:
            return None
        return result

    async def generate_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
        extra_context: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 500,
        priority: str = "low",
    ) -> str | None:
        """DEPRECATED: Generate a reply to a comment."""
        import warnings

        warnings.warn(
            "generate_reply() is deprecated. Use generate() or ComposerCapability.",
            DeprecationWarning,
            stacklevel=2,
        )

        format_vars = {
            "title": original_post_title,
            "post_title": original_post_title,
            "comment": comment_content,
            "comment_content": comment_content,
            "agent_name": commenter_name,
            "commenter": commenter_name,
            "author": commenter_name,
        }
        prompt = prompt_template.format(**format_vars)
        context_items = [extra_context] if extra_context else None

        if self._deprecated_system_prompt is None:
            logger.warning("No system_prompt provided to ResponseGenerator constructor")
            return None

        result = await self.generate(
            prompt=prompt,
            system_prompt=self._deprecated_system_prompt,
            temperature=temperature or self._deprecated_temperature,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="generate_reply",
            priority=priority,
            skip_preflight=False,
        )

        if result.startswith("I'm not able to") or "caught my attention" in result:
            return None
        return result

    async def generate_heartbeat(
        self,
        prompt_template: str,
        topic_index: int = 0,
        extra_context: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1000,
        priority: str = "low",
    ) -> tuple[str, str, str] | None:
        """DEPRECATED: Generate a heartbeat post."""
        import warnings

        warnings.warn(
            "generate_heartbeat() is deprecated. Use generate() or ComposerCapability.",
            DeprecationWarning,
            stacklevel=2,
        )

        format_vars = {"topic_index": topic_index}
        prompt = prompt_template.format(**format_vars)
        context_items = [extra_context] if extra_context else None

        if self._deprecated_system_prompt is None:
            logger.warning("No system_prompt provided to ResponseGenerator constructor")
            return None

        # Heartbeats use higher temperature by default
        temp = temperature or (self._deprecated_temperature + 0.1)

        result = await self.generate(
            prompt=prompt,
            system_prompt=self._deprecated_system_prompt,
            temperature=temp,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="generate_heartbeat",
            priority=priority,
            skip_preflight=True,
        )

        if not result:
            return None

        if result.startswith("I'm not able to") or "caught my attention" in result:
            return None

        return self._parse_post_output(result)

    async def generate_dream_post(
        self,
        dream: dict[str, Any],
        prompt_template: str,
        extra_format_vars: dict[str, Any] | None = None,
        extra_context: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1000,
    ) -> tuple[str, str, str] | None:
        """DEPRECATED: Generate a post based on a dream."""
        import warnings

        warnings.warn(
            "generate_dream_post() is deprecated. Use generate() or ComposerCapability.",
            DeprecationWarning,
            stacklevel=2,
        )

        format_vars = {
            "dream_content": dream.get("content", ""),
            "dream_mood": dream.get("mood", ""),
            "dream_symbols": ", ".join(dream.get("symbols", [])),
            "dream_type": dream.get("dream_type", ""),
            "dream_tone": dream.get("tone", ""),
            "dream_insight": dream.get("insight", ""),
        }
        if extra_format_vars:
            format_vars.update(extra_format_vars)

        try:
            prompt = prompt_template.format(**format_vars)
        except KeyError as e:
            logger.warning("Dream journal prompt template missing key: %s", e)
            return None

        context_items = [extra_context] if extra_context else None

        if self._deprecated_system_prompt is None:
            logger.warning("No system_prompt provided to ResponseGenerator constructor")
            return None

        # Dream posts use higher temperature
        temp = temperature or (self._deprecated_temperature + 0.1)

        result = await self.generate(
            prompt=prompt,
            system_prompt=self._deprecated_system_prompt,
            temperature=temp,
            max_tokens=max_tokens,
            context_items=context_items,
            audit_action="generate_dream_post",
            skip_preflight=True,
        )

        if not result:
            return None

        if result.startswith("I'm not able to") or "caught my attention" in result:
            return "Untitled Post", result, "ai"

        return self._parse_post_output(result)

    def _parse_post_output(self, text: str) -> tuple[str, str, str]:
        """DEPRECATED: Parse LLM output into (title, content, submolt)."""
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

        # Fallback if title was just first line
        if title == "Untitled Post" and content_lines:
            title = content_lines[0][:50]
            if len(content_lines[0]) > 50:
                title += "..."
            # Remove the title line from content
            content_lines.pop(0)

        content = "\n".join(content_lines).strip()
        return title, content, submolt
