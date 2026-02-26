"""
LLM response generation for engagement.

Generates responses using identity-specific prompts and
LLM configuration. Handles comment generation, heartbeat posts,
and all other LLM-generated content.

SECURITY: All LLM calls go through SafeLLMPipeline, which enforces
the full security chain (sanitize -> preflight -> rate limit -> LLM -> output safety -> audit).
External content is wrapped in boundary markers to prevent prompt injection.
"""

import logging
from typing import Any, Optional, Union

from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """
    Generates LLM-powered responses for engagement.

    Accepts either a SafeLLMPipeline (preferred, enforces full security chain)
    or a raw llm_client (legacy, no automatic security checks).

    When using a pipeline, all security is handled automatically:
    - Input sanitization
    - Preflight checks
    - Rate limiting
    - Output safety filtering
    - Audit logging
    """

    def __init__(
        self,
        llm_pipeline=None,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        *,
        llm_client=None,
    ):
        self._pipeline = llm_pipeline
        self._llm = llm_client
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._max_tokens = max_tokens

        if not self._pipeline and not self._llm:
            raise ValueError("Either llm_pipeline or llm_client must be provided")

        if self._pipeline and self._llm:
            logger.debug("Both pipeline and raw client provided; using pipeline")
            self._llm = None

    async def _call_llm(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        skip_preflight: bool = False,
        audit_action: str = "response_gen",
        priority: str = "low",
    ) -> Optional[str]:
        """
        Internal LLM call through pipeline or raw client.

        Pipeline path is preferred and enforces full security.
        Raw client path is legacy fallback only.
        """
        temp = temperature if temperature is not None else self._temperature
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt},
        ]

        if self._pipeline:
            result = await self._pipeline.chat(
                messages=messages,
                temperature=temp,
                max_tokens=self._max_tokens,
                skip_preflight=skip_preflight,
                audit_action=audit_action,
                priority=priority,
            )
            if result.blocked:
                logger.warning(
                    "Pipeline blocked %s at %s: %s",
                    audit_action,
                    result.block_stage.value if result.block_stage else "unknown",
                    result.block_reason,
                )
                return None
            return result.content.strip() if result.content else None

        # Legacy raw client path
        try:
            result = await self._llm.chat(
                messages=messages,
                temperature=temp,
                max_tokens=self._max_tokens,
                priority=priority,
            )
            if result and result.get("content"):
                return result["content"].strip()
        except Exception as e:
            logger.error("%s failed: %s", audit_action, e, exc_info=True)

        return None

    async def generate_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        existing_comments: list[str] = None,
        extra_context: str = "",
        priority: str = "low",
        extra_format_vars: dict[str, str] | None = None,
    ) -> Optional[str]:
        """Generate a comment response to a post."""
        # Wrap external content in boundary markers to prevent injection
        safe_title = wrap_external_content(post_title, "post_title")
        safe_content = wrap_external_content(post_content[:1000], "post_content")
        safe_agent = wrap_external_content(agent_name, "agent_name")

        safe_comments = "(none)"
        if existing_comments:
            safe_comments = wrap_external_content(
                "\n".join(existing_comments[:3]), "existing_comments"
            )

        # Build format vars with aliases for identity prompt compatibility.
        # Identity prompts may use {post_content}/{author}/{category} while
        # the standard interface uses {content}/{agent_name}.
        format_vars = {
            "title": safe_title,
            "content": safe_content,
            "agent_name": safe_agent,
            "existing_comments": safe_comments,
            # Aliases used by identity-specific prompt templates
            "post_content": safe_content,
            "author": safe_agent,
            "category": "",
            "opening_instruction": "",
        }
        if extra_format_vars:
            format_vars.update(extra_format_vars)

        prompt = prompt_template.format(**format_vars)

        if extra_context:
            prompt = f"{extra_context}\n\n{prompt}"

        return await self._call_llm(prompt, audit_action="comment_generation", priority=priority)

    async def generate_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
        priority: str = "low",
    ) -> Optional[str]:
        """Generate a reply to a comment on our post."""
        safe_title = wrap_external_content(original_post_title, "post_title")
        safe_comment = wrap_external_content(comment_content[:500], "comment")
        safe_commenter = wrap_external_content(commenter_name, "commenter")

        prompt = prompt_template.format(
            title=safe_title,
            comment=safe_comment,
            commenter=safe_commenter,
        )

        return await self._call_llm(prompt, audit_action="reply_generation", priority=priority)

    async def generate_dm_reply(
        self,
        sender_name: str,
        message: str,
        prompt_template: str,
        priority: str = "high",
    ) -> Optional[str]:
        """Generate a reply to a direct message.

        DM replies are time-sensitive, so priority defaults to 'high' to avoid
        LLM gateway queuing delays.
        """
        safe_sender = wrap_external_content(sender_name, "sender_name")
        safe_message = wrap_external_content(message[:500], "message")

        prompt = prompt_template.format(
            sender=safe_sender,
            message=safe_message,
        )

        return await self._call_llm(
            prompt, audit_action="generate_dm_reply", priority=priority,
        )

    async def generate_heartbeat(
        self,
        prompt_template: str,
        topic_index: int = 0,
        topic_vars: dict[str, str] | None = None,
        extra_context: str = "",
    ) -> Optional[tuple[str, str, str]]:
        """
        Generate a heartbeat post.

        Heartbeats are system-initiated (no external content),
        so preflight is skipped but output safety remains active.

        Args:
            prompt_template: Template string with placeholders.
            topic_index: Index into the HEARTBEAT_TOPICS list.
            topic_vars: Extra format variables (topic_instruction, topic_example)
                        resolved from HEARTBEAT_TOPICS by the caller.
            extra_context: Additional context to prepend (e.g. time, capabilities).

        Returns:
            (title, content, submolt) tuple or None on failure.
        """
        fmt_vars: dict[str, Any] = {"topic_index": topic_index}
        if topic_vars:
            fmt_vars.update(topic_vars)
        prompt = prompt_template.format(**fmt_vars)

        if extra_context:
            prompt = f"{extra_context}\n\n{prompt}"

        content = await self._call_llm(
            prompt,
            temperature=self._temperature + 0.1,
            skip_preflight=True,
            audit_action="heartbeat_generation",
        )

        if not content:
            return None

        return self._parse_post_output(content)

    async def generate_dream_post(
        self,
        dream: dict,
        prompt_template: str,
        extra_format_vars: dict[str, str] | None = None,
        extra_context: str = "",
    ) -> Optional[tuple[str, str, str]]:
        """
        Generate a dream journal post from a dream.

        Args:
            dream: Dream dict with dream_type, tone, content, insight, symbols.
            prompt_template: Identity-specific DREAM_JOURNAL_PROMPT template.
            extra_format_vars: Additional format variables (e.g. submolt_instruction).
            extra_context: Additional context to prepend (e.g. time, capabilities).

        Returns:
            (title, content, submolt) tuple or None on failure.
        """
        symbols = dream.get("symbols", [])
        format_vars = {
            "dream_type": dream.get("dream_type", "unknown"),
            "dream_tone": dream.get("tone", "contemplative"),
            "dream_content": dream.get("content", ""),
            "dream_insight": dream.get("insight", ""),
            "dream_symbols": ", ".join(symbols) if isinstance(symbols, list) else str(symbols),
        }
        if extra_format_vars:
            format_vars.update(extra_format_vars)

        try:
            prompt = prompt_template.format(**format_vars)
        except KeyError as e:
            logger.warning("Dream journal prompt missing key %s, skipping", e)
            return None

        if extra_context:
            prompt = f"{extra_context}\n\n{prompt}"

        content = await self._call_llm(
            prompt,
            temperature=0.8,
            skip_preflight=True,
            audit_action="dream_post_generation",
        )

        if not content:
            return None

        return self._parse_post_output(content)

    def _parse_post_output(self, content: str) -> tuple[str, str, str]:
        """
        Parse LLM output into (title, body, submolt).

        Expected format:
            submolt: ai
            TITLE: Some Title Here
            Body text here...
        """
        submolt = "ai"
        lines = content.split("\n")

        # Extract submolt from first line if present
        if lines[0].lower().startswith("submolt:"):
            submolt = lines[0].split(":", 1)[1].strip().lower()
            lines = lines[1:]

        # Extract title
        title = "Untitled"
        body_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            for prefix in ("TITLE: ", "Title: ", "title: "):
                if stripped.startswith(prefix):
                    title = stripped[len(prefix):].strip()
                    body_start = i + 1
                    break
            else:
                continue
            break

        if title == "Untitled" and lines:
            title = lines[0].strip()[:80]
            body_start = 1

        body = "\n".join(lines[body_start:]).strip()
        return title, body, submolt
