"""
LLM response generation for Moltbook engagement.

Generates responses using identity-specific prompts and
LLM configuration. Handles comment generation, heartbeat posts,
and all other LLM-generated content.
"""

import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


class ResponseGenerator:
    """
    Generates LLM-powered responses for Moltbook engagement.

    Uses identity-specific prompts loaded from the identity's prompts module.
    """

    def __init__(
        self,
        llm_client,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        self._llm = llm_client
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def generate_comment(
        self,
        post_title: str,
        post_content: str,
        agent_name: str,
        prompt_template: str,
        existing_comments: list[str] = None,
        extra_context: str = "",
    ) -> Optional[str]:
        """Generate a comment response to a post."""
        prompt = prompt_template.format(
            title=post_title,
            content=post_content[:1000],
            agent_name=agent_name,
            existing_comments="\n".join(existing_comments[:3]) if existing_comments else "(none)",
        )

        if extra_context:
            prompt = f"{extra_context}\n\n{prompt}"

        try:
            result = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if result and result.get("content"):
                return result["content"].strip()
        except Exception as e:
            logger.error("Comment generation failed: %s", e)

        return None

    async def generate_reply(
        self,
        original_post_title: str,
        comment_content: str,
        commenter_name: str,
        prompt_template: str,
    ) -> Optional[str]:
        """Generate a reply to a comment on our post."""
        prompt = prompt_template.format(
            title=original_post_title,
            comment=comment_content[:500],
            commenter=commenter_name,
        )

        try:
            result = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if result and result.get("content"):
                return result["content"].strip()
        except Exception as e:
            logger.error("Reply generation failed: %s", e)

        return None

    async def generate_heartbeat(
        self,
        prompt_template: str,
        topic_index: int = 0,
    ) -> Optional[tuple[str, str, str]]:
        """
        Generate a heartbeat post.

        Returns:
            (title, content, submolt) tuple or None on failure.
        """
        prompt = prompt_template.format(topic_index=topic_index)

        try:
            result = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._temperature + 0.1,  # Slightly more creative
                max_tokens=self._max_tokens,
            )
            if not result or not result.get("content"):
                return None

            content = result["content"].strip()
            return self._parse_post_output(content)

        except Exception as e:
            logger.error("Heartbeat generation failed: %s", e)
            return None

    async def generate_dream_post(
        self,
        dream_content: str,
        dream_insight: str,
        prompt_template: str,
    ) -> Optional[tuple[str, str, str]]:
        """Generate a dream journal post."""
        prompt = prompt_template.format(
            dream_content=dream_content,
            dream_insight=dream_insight,
        )

        try:
            result = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.8,
                max_tokens=self._max_tokens,
            )
            if not result or not result.get("content"):
                return None

            return self._parse_post_output(result["content"].strip())

        except Exception as e:
            logger.error("Dream post generation failed: %s", e)
            return None

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
