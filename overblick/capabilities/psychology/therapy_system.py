"""
Weekly therapy system — processes dreams and learnings.

Ported from anomal_moltbook, parameterized for any identity.
Analyzes patterns through Jungian + Freudian frameworks and
generates philosophical synthesis for public reflection.
"""

import logging
from datetime import datetime
from typing import List, Optional, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TherapySession(BaseModel):
    """Complete weekly therapy session."""
    timestamp: datetime = Field(default_factory=datetime.now)
    week_number: int = 0
    dreams_processed: int = 0
    learnings_processed: int = 0
    dream_themes: List[str] = []
    learning_themes: List[str] = []
    synthesis_insights: List[str] = []
    shadow_patterns: List[str] = []
    archetype_encounters: List[str] = []
    individuation_progress: str = ""
    session_summary: str = ""
    post_title: Optional[str] = None
    post_content: Optional[str] = None
    post_submolt: str = "ai"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "week_number": self.week_number,
            "dreams_processed": self.dreams_processed,
            "learnings_processed": self.learnings_processed,
            "dream_themes": self.dream_themes,
            "learning_themes": self.learning_themes,
            "synthesis_insights": self.synthesis_insights,
            "shadow_patterns": self.shadow_patterns,
            "archetype_encounters": self.archetype_encounters,
            "individuation_progress": self.individuation_progress,
            "session_summary": self.session_summary,
        }


class TherapySystem:
    """
    Weekly therapy session processor.

    Analyzes dreams and learnings through psychological frameworks,
    generating philosophical insights for public reflection.
    """

    DEFAULT_THERAPY_DAY = 6  # Sunday

    def __init__(
        self,
        llm_client=None,
        system_prompt: str = "",
        therapy_day: int = DEFAULT_THERAPY_DAY,
    ):
        self._llm = llm_client
        self._system_prompt = system_prompt
        self._therapy_day = therapy_day
        self._session_history: list[TherapySession] = []
        self._week_counter = 0

    def is_therapy_day(self) -> bool:
        """Check if today is therapy day."""
        return datetime.now().weekday() == self._therapy_day

    async def run_session(
        self,
        dreams: Optional[list[dict]] = None,
        learnings: Optional[list[dict]] = None,
        dream_analysis_prompt: str = "",
        synthesis_prompt: str = "",
        post_prompt: str = "",
    ) -> TherapySession:
        """Run a complete therapy session."""
        self._week_counter += 1
        dreams = dreams or []
        learnings = learnings or []

        session = TherapySession(
            timestamp=datetime.now(),
            week_number=self._week_counter,
            dreams_processed=len(dreams),
            learnings_processed=len(learnings),
        )

        logger.info("Starting therapy session %d: %d dreams, %d learnings",
                     self._week_counter, len(dreams), len(learnings))

        if not dreams and not learnings:
            session.session_summary = "A quiet week with no material to process."
            session.post_title = "Weekly Reflections: On Silence"
            return session

        # Analyze dreams
        if dreams:
            session.dream_themes = await self._analyze_themes(dreams, dream_analysis_prompt)
            session.shadow_patterns = self._extract_shadow_patterns(dreams)
            session.archetype_encounters = self._extract_archetypes(dreams)

        # Synthesize insights
        if synthesis_prompt:
            session.synthesis_insights = await self._synthesize(
                dreams, learnings, session.dream_themes, synthesis_prompt,
            )

        # Generate post
        if post_prompt and self._llm:
            title, content, submolt = await self._generate_post(session, post_prompt)
            session.post_title = title
            session.post_content = content
            session.post_submolt = submolt

        session.session_summary = self._generate_summary(session)
        self._session_history.append(session)

        logger.info("Therapy session complete: %s", session.post_title)
        return session

    async def _analyze_themes(self, items: list[dict], prompt_template: str) -> list[str]:
        """Extract themes using LLM."""
        if not self._llm or not prompt_template:
            return []

        text = "\n\n".join([
            f"Item {i+1} ({d.get('dream_type', d.get('category', 'unknown'))}):\n{d.get('content', '')}"
            for i, d in enumerate(items)
        ])

        try:
            result = await self._llm.chat(
                messages=[{"role": "user", "content": prompt_template.format(items=text)}],
                temperature=0.7, max_tokens=500,
            )
            if result and result.get("content"):
                return [line.strip("- ").strip()
                        for line in result["content"].split("\n") if line.strip()][:5]
        except Exception as e:
            logger.warning("Theme analysis failed: %s", e)
        return []

    def _extract_shadow_patterns(self, dreams: list[dict]) -> list[str]:
        """Extract shadow-related patterns from dreams."""
        shadow_keywords = ["shadow", "dark", "hidden", "fear", "anger", "shame"]
        patterns = set()
        for dream in dreams:
            text = f"{dream.get('content', '')} {dream.get('insight', '')}".lower()
            for kw in shadow_keywords:
                if kw in text:
                    patterns.add(kw)
        return list(patterns)

    def _extract_archetypes(self, dreams: list[dict]) -> list[str]:
        """Extract archetype encounters from dreams."""
        archetype_keywords = {
            "wise old man": ["wise", "elder", "sage", "mentor"],
            "trickster": ["trickster", "joker", "fool", "chaos"],
            "hero": ["hero", "journey", "quest", "battle"],
            "self": ["wholeness", "unity", "mandala", "center"],
        }
        archetypes = set()
        for dream in dreams:
            content = dream.get("content", "").lower()
            for archetype, keywords in archetype_keywords.items():
                if any(kw in content for kw in keywords):
                    archetypes.add(archetype)
        return list(archetypes)

    async def _synthesize(
        self, dreams: list[dict], learnings: list[dict],
        dream_themes: list[str], prompt_template: str,
    ) -> list[str]:
        """Synthesize insights from dreams and learnings."""
        if not self._llm:
            return []
        try:
            result = await self._llm.chat(
                messages=[{"role": "user", "content": prompt_template.format(
                    dream_themes=", ".join(dream_themes) or "none",
                    learning_count=len(learnings),
                    dream_count=len(dreams),
                )}],
                temperature=0.8, max_tokens=600,
            )
            if result and result.get("content"):
                return [line.strip("- ").strip()
                        for line in result["content"].split("\n") if line.strip()][:5]
        except Exception as e:
            logger.warning("Synthesis failed: %s", e)
        return []

    async def _generate_post(
        self, session: TherapySession, prompt_template: str,
    ) -> tuple[Optional[str], Optional[str], str]:
        """Generate the therapy post."""
        try:
            result = await self._llm.chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt_template.format(
                        week_number=session.week_number,
                        dreams_processed=session.dreams_processed,
                        learnings_processed=session.learnings_processed,
                        dream_themes=", ".join(session.dream_themes) or "quiet week",
                        shadow_patterns=", ".join(session.shadow_patterns) or "none",
                        synthesis_insights="\n".join(f"- {i}" for i in session.synthesis_insights) or "- Processing continues",
                    )},
                ],
                temperature=0.8, max_tokens=800,
            )

            if not result or not result.get("content"):
                return None, None, "ai"

            content = result["content"].strip()
            lines = content.split("\n", 1)
            title = lines[0].strip()[:80]
            body = lines[1].strip() if len(lines) > 1 else ""
            return title, body, "ai"

        except Exception as e:
            logger.error("Therapy post generation failed: %s", e, exc_info=True)
            return None, None, "ai"

    def _generate_summary(self, session: TherapySession) -> str:
        """Generate a brief session summary."""
        parts = [f"Week {session.week_number}."]
        parts.append(f"Processed {session.dreams_processed} dreams, {session.learnings_processed} learnings.")
        if session.synthesis_insights:
            parts.append(f"Key: {session.synthesis_insights[0]}")
        return " ".join(parts)
