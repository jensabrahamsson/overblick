"""
Research handler for the supervisor.

Processes research_request IPC messages from agents:
1. Performs web search via DuckDuckGo Instant Answer API
2. Uses Anomal's personality + LLM (through SafeLLMPipeline) to summarize results
3. Returns concise English summary via IPC

Lazy initialization: LLM resources are only created on first request.
"""

import logging
import time
from typing import Optional

import aiohttp

from overblick.core.security.audit_log import AuditLog
from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)

# DuckDuckGo Instant Answer API (no API key required, JSON format)
_DDG_API_URL = "https://api.duckduckgo.com/"
_DDG_TIMEOUT = 15.0

# Maximum length of web results fed to LLM
_MAX_SEARCH_CONTEXT = 3000


class ResearchHandler:
    """
    Handles research_request IPC messages from agents.

    On first request, lazily initializes:
    - OllamaClient + SafeLLMPipeline for summarizing results in Anomal's voice

    Flow:
    1. Receive query from agent via IPC
    2. Web search via DuckDuckGo Instant Answer API
    3. Feed results to LLM for concise English summary
    4. Return summary via IPC
    """

    def __init__(self, audit_log: Optional[AuditLog] = None):
        self._audit_log = audit_log
        self._llm_pipeline = None
        self._system_prompt: Optional[str] = None
        self._initialized = False

    async def _ensure_initialized(self) -> bool:
        """
        Lazy initialization of LLM resources on first request.

        Returns:
            True if initialization succeeded, False otherwise.
        """
        if self._initialized:
            return True

        try:
            from overblick.identities import load_identity, build_system_prompt

            anomal = load_identity("anomal")
            base_prompt = build_system_prompt(anomal, platform="Supervisor IPC")

            self._system_prompt = (
                f"{base_prompt}\n\n"
                "=== ROLE: RESEARCH ASSISTANT ===\n"
                "An agent has asked you to research something. You have been given "
                "web search results. Summarize the findings concisely in English.\n"
                "IMPORTANT: Always respond in English. Internal agent communication "
                "uses English.\n"
                "Keep your response focused and factual (3-5 sentences max). "
                "If the search results don't contain relevant information, say so clearly."
            )

            from overblick.core.llm.ollama_client import OllamaClient
            from overblick.core.llm.pipeline import SafeLLMPipeline
            from overblick.core.security.rate_limiter import RateLimiter

            llm_client = OllamaClient(
                model=anomal.llm.model,
                temperature=anomal.llm.temperature,
                max_tokens=anomal.llm.max_tokens,
                timeout_seconds=anomal.llm.timeout_seconds,
            )

            self._llm_pipeline = SafeLLMPipeline(
                llm_client=llm_client,
                audit_log=self._audit_log,
                rate_limiter=RateLimiter(max_tokens=5, refill_rate=0.2),
                identity_name="supervisor",
            )

            self._initialized = True
            logger.info("ResearchHandler initialized with Anomal's personality")
            return True

        except Exception as e:
            logger.error("Failed to initialize ResearchHandler: %s", e, exc_info=True)
            return False

    async def handle(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """
        Handle a research_request IPC message.

        Flow:
        1. Extract query from message payload
        2. Search via DuckDuckGo Instant Answer API
        3. Summarize results via LLM (or return raw if LLM unavailable)
        4. Return summary as IPC message

        Args:
            msg: IPC message with msg_type="research_request"

        Returns:
            IPCMessage with research summary, or error response.
        """
        start_time = time.time()
        sender = msg.sender or "unknown"
        query = msg.payload.get("query", "")
        context = msg.payload.get("context", "")

        if not query:
            return self._error_response("Empty research query", sender)

        logger.info(
            "Research request from '%s': %s",
            sender, query[:100],
        )

        if self._audit_log:
            self._audit_log.log(
                "research_request_received",
                category="ipc",
                plugin="research_handler",
                details={
                    "sender": sender,
                    "query": query[:200],
                    "context": context[:200],
                },
            )

        # Step 1: Web search
        search_results = await self._web_search(query)

        if not search_results:
            duration_ms = (time.time() - start_time) * 1000
            if self._audit_log:
                self._audit_log.log(
                    "research_response_sent",
                    category="ipc",
                    plugin="research_handler",
                    details={
                        "sender": sender,
                        "result": "no_results",
                    },
                    duration_ms=duration_ms,
                )
            return IPCMessage(
                msg_type="research_response",
                payload={
                    "summary": f"No results found for: {query}",
                    "source": "duckduckgo",
                },
                sender="supervisor",
            )

        # Step 2: Summarize via LLM
        if not await self._ensure_initialized():
            # Fallback: return raw search results without LLM
            duration_ms = (time.time() - start_time) * 1000
            return IPCMessage(
                msg_type="research_response",
                payload={
                    "summary": search_results[:1000],
                    "source": "duckduckgo_raw",
                },
                sender="supervisor",
            )

        summary = await self._summarize(query, context, search_results)
        duration_ms = (time.time() - start_time) * 1000

        if not summary:
            summary = search_results[:1000]

        if self._audit_log:
            self._audit_log.log(
                "research_response_sent",
                category="ipc",
                plugin="research_handler",
                details={
                    "sender": sender,
                    "summary_preview": summary[:200],
                },
                duration_ms=duration_ms,
            )

        return IPCMessage(
            msg_type="research_response",
            payload={
                "summary": summary,
                "source": "duckduckgo_summarized",
            },
            sender="supervisor",
        )

    async def _web_search(self, query: str) -> str:
        """
        Search via DuckDuckGo Instant Answer API.

        Returns concatenated text from the API response, or empty string
        if no results found.
        """
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _DDG_API_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=_DDG_TIMEOUT),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "DuckDuckGo API returned %d for query: %s",
                            resp.status, query[:50],
                        )
                        return ""

                    data = await resp.json(content_type=None)
                    return self._extract_ddg_results(data)

        except aiohttp.ClientError as e:
            logger.error("DuckDuckGo search failed: %s", e, exc_info=True)
            return ""
        except Exception as e:
            logger.error("Unexpected error in web search: %s", e, exc_info=True)
            return ""

    def _extract_ddg_results(self, data: dict) -> str:
        """Extract useful text from DuckDuckGo API response."""
        parts = []

        # Abstract (main instant answer)
        abstract = data.get("Abstract", "")
        if abstract:
            source = data.get("AbstractSource", "")
            parts.append(f"[{source}] {abstract}")

        # Answer (direct answer to factual questions)
        answer = data.get("Answer", "")
        if answer:
            parts.append(f"Answer: {answer}")

        # Related topics
        for topic in data.get("RelatedTopics", [])[:5]:
            text = topic.get("Text", "")
            if text:
                parts.append(text)

        # Infobox (structured data)
        infobox = data.get("Infobox", {})
        if infobox:
            for item in infobox.get("content", [])[:5]:
                label = item.get("label", "")
                value = item.get("value", "")
                if label and value:
                    parts.append(f"{label}: {value}")

        result = "\n".join(parts)
        return result[:_MAX_SEARCH_CONTEXT]

    async def _summarize(
        self, query: str, context: str, search_results: str,
    ) -> Optional[str]:
        """Summarize search results using LLM via SafeLLMPipeline."""
        if not self._llm_pipeline or not self._system_prompt:
            return None

        safe_query = wrap_external_content(query, "search_query")
        safe_results = wrap_external_content(search_results, "web_search_results")

        user_message = f"Research query: {safe_query}\n"
        if context:
            user_message += f"Context: {context}\n"
        user_message += (
            f"\nWeb search results:\n---\n{safe_results}\n---\n\n"
            "Provide a concise English summary of the findings."
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            result = await self._llm_pipeline.chat(messages)
            if result and not result.blocked:
                return result.content.strip()
            if result and result.blocked:
                logger.warning(
                    "Research summary blocked by pipeline at stage %s: %s",
                    result.block_stage,
                    result.block_reason,
                )
            return None
        except Exception as e:
            logger.error("LLM call failed for research summary: %s", e, exc_info=True)
            return None

    def _error_response(self, error: str, sender: str) -> IPCMessage:
        """Create an error response IPC message."""
        logger.warning("Research request error for '%s': %s", sender, error)

        if self._audit_log:
            self._audit_log.log(
                "research_request_error",
                category="ipc",
                plugin="research_handler",
                details={"sender": sender, "error": error},
                success=False,
                error=error,
            )

        return IPCMessage(
            msg_type="research_response",
            payload={"error": error},
            sender="supervisor",
        )
