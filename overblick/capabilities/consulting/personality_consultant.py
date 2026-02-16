"""
PersonalityConsultantCapability — consult any personality for advice.

Loads a personality's YAML, builds its system prompt, and makes an LLM
call through the existing SafeLLMPipeline. The consulted personality
does not need to be "running" — we simply borrow its perspective.

Pattern: same as SummarizerCapability (LLM call via pipeline, returns result).
"""

import logging
from typing import Any, Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class PersonalityConsultantCapability(CapabilityBase):
    """
    Consult another personality for advice via LLM.

    Any agent can use this to get a second opinion from any personality
    in the stable. The consulted personality is loaded lazily and cached.

    Config options (in personality YAML under personality_consultant):
        default_consultant: str  — fallback personality name (default: "cherry")
        temperature: float       — LLM temperature for consultations (default: 0.7)
        max_tokens: int          — max response length (default: 800)
    """

    name = "personality_consultant"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._default_consultant: str = "cherry"
        self._temperature: float = 0.7
        self._max_tokens: int = 800
        self._personality_cache: dict[str, Any] = {}
        self._prompt_cache: dict[str, str] = {}

    async def setup(self) -> None:
        """Load config from personality YAML."""
        self._default_consultant = self.ctx.config.get(
            "default_consultant", "cherry",
        )
        self._temperature = self.ctx.config.get("temperature", 0.7)
        self._max_tokens = self.ctx.config.get("max_tokens", 800)
        logger.info(
            "PersonalityConsultantCapability initialized for %s "
            "(default consultant: %s)",
            self.ctx.identity_name,
            self._default_consultant,
        )

    async def consult(
        self,
        query: str,
        context: str = "",
        consultant_name: str = "",
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        """
        Consult a personality for advice.

        Args:
            query: The question or prompt for the consultant.
            context: Additional context to include in the user message.
            consultant_name: Which personality to consult (default from config).
            temperature: Override LLM temperature for this call.

        Returns:
            The consultant's response text, or None if unavailable/blocked.
        """
        name = consultant_name or self._default_consultant

        # Build consultant's system prompt (lazy-load personality)
        system_prompt = self._get_system_prompt(name)
        if system_prompt is None:
            return None

        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            logger.warning(
                "PersonalityConsultant: no LLM pipeline available",
            )
            return None

        # Build messages with consultant's system prompt
        user_content = query
        if context:
            user_content = f"{query}\n\nContext:\n{context}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            result = await pipeline.chat(
                messages=messages,
                temperature=temperature or self._temperature,
                max_tokens=self._max_tokens,
                skip_preflight=True,  # Internal system-generated content
                audit_action=f"consult_{name}",
                priority="low",
            )
            if result.blocked:
                logger.warning(
                    "PersonalityConsultant: %s consultation blocked: %s",
                    name,
                    result.block_reason,
                )
                return None
            return result.content.strip() if result.content else None
        except Exception as e:
            logger.error(
                "PersonalityConsultant: %s consultation failed: %s", name, e,
            )
            return None

    def _get_system_prompt(self, name: str) -> Optional[str]:
        """Get cached system prompt for a personality, loading if needed."""
        if name in self._prompt_cache:
            return self._prompt_cache[name]

        personality = self._load_identity(name)
        if personality is None:
            return None

        from overblick.identities import build_system_prompt

        prompt = build_system_prompt(
            personality, platform="Internal Consultation",
        )
        self._prompt_cache[name] = prompt
        return prompt

    def _load_identity(self, name: str) -> Any:
        """Load and cache a personality by name."""
        if name in self._personality_cache:
            return self._personality_cache[name]

        from overblick.identities import load_identity

        try:
            personality = load_identity(name)
            self._personality_cache[name] = personality
            logger.info(
                "PersonalityConsultant: loaded personality '%s'", name,
            )
            return personality
        except FileNotFoundError:
            logger.warning(
                "PersonalityConsultant: personality '%s' not found", name,
            )
            return None
