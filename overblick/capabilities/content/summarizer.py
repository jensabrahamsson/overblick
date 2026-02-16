"""
SummarizerCapability — LLM-powered text summarization.

Uses SafeLLMPipeline to generate concise summaries of text content.
Useful for condensing long posts, articles, or conversation history.
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = """Summarize the following text in {max_length} words or fewer.
Be concise and capture the key points.

Text:
{text}

Summary:"""


class SummarizerCapability(CapabilityBase):
    """
    Text summarization capability.

    Uses the SafeLLMPipeline to generate concise summaries.
    Configurable max length and temperature.
    """

    name = "summarizer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._temperature: float = 0.3
        self._max_tokens: int = 500

    async def setup(self) -> None:
        self._temperature = self.ctx.config.get("temperature", 0.3)
        self._max_tokens = self.ctx.config.get("max_tokens", 500)
        logger.info("SummarizerCapability initialized for %s", self.ctx.identity_name)

    async def summarize(self, text: str, max_length: int = 100) -> Optional[str]:
        """
        Summarize text using the LLM pipeline.

        Args:
            text: Text to summarize.
            max_length: Target maximum word count for the summary.

        Returns:
            Summary string, or None if LLM is unavailable or fails.
        """
        if not text or not text.strip():
            return ""

        pipeline = self.ctx.llm_pipeline
        llm_client = self.ctx.llm_client

        prompt = _SUMMARIZE_PROMPT.format(text=text[:3000], max_length=max_length)

        if pipeline:
            try:
                result = await pipeline.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    skip_preflight=True,
                    audit_action="summarize",
                )
                if result.blocked:
                    logger.warning("Summarization blocked: %s", result.block_reason)
                    return None
                return result.content.strip() if result.content else None
            except Exception as e:
                logger.error("Summarization pipeline error: %s", e, exc_info=True)
                return None

        if llm_client:
            try:
                result = await llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                if result and result.get("content"):
                    return result["content"].strip()
            except Exception as e:
                logger.error("Summarization LLM error: %s", e, exc_info=True)

        logger.warning("SummarizerCapability: no LLM available")
        return None
