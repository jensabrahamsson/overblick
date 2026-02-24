"""
StyleTrainerCapability — learn the principal's writing style from example emails.

Ingests example emails, uses Deepseek (via LLM Gateway with complexity="ultra")
to extract a comprehensive writing style profile, and provides prompt snippets
for reply generation that mimic the principal's voice.

Security:
- No external I/O — operates on locally provided email text
- LLM calls go through SafeLLMPipeline (sanitize → preflight → rate limit → LLM → output safety → audit)
- Style profile persisted as JSON in the identity's data directory

Status: INACTIVE by default. Must be explicitly enabled in identity config.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)

# LLM complexity level — routes to Deepseek via Gateway for deep analysis
STYLE_ANALYSIS_COMPLEXITY = "ultra"

STYLE_EXTRACTION_PROMPT = """\
Analyze the following {count} example emails written by the same person.
Extract a comprehensive writing style profile as a JSON object with these keys:

- "avg_sentence_length": approximate average sentence length (words)
- "paragraph_structure": typical paragraph pattern (e.g. "short 1-2 sentence paragraphs")
- "greeting_patterns": dict mapping language codes (e.g. "en", "sv") to typical greetings
- "closing_patterns": dict mapping language codes to typical sign-offs
- "formality_by_context": dict with keys "known_contacts" and "unknown_contacts", each describing formality level
- "language_switching": description of when and how the person switches between languages
- "vocabulary_preferences": list of characteristic words/phrases the person favors
- "vocabulary_avoided": list of words/phrases the person never uses
- "tone_markers": list of tone descriptors (e.g. "direct", "warm", "formal", "casual")
- "punctuation_habits": any notable punctuation patterns (e.g. "uses em-dashes frequently")
- "signature_style": typical email signature format

Return ONLY valid JSON. No markdown formatting, no explanation.

Example emails:
{emails}
"""

STYLE_PROMPT_TEMPLATE = """\
=== PRINCIPAL'S WRITING STYLE ===
Mimic the following writing style when composing replies:
- Tone: {tone}
- Formality: {formality}
- Sentence length: ~{avg_length} words
- Greeting style: {greetings}
- Closing style: {closings}
- Preferred vocabulary: {vocabulary}
- Avoid these words: {avoided}
{extra}
"""


class StyleTrainerCapability(CapabilityBase):
    """
    Learn and reproduce the principal's email writing style.

    Ingests example emails, extracts style features via LLM (Deepseek),
    and provides style prompts for reply generation.
    """

    name = "style_trainer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._profile: Optional[dict[str, Any]] = None
        self._profile_path: Optional[Path] = None
        self._min_examples: int = 20

    async def setup(self) -> None:
        """Initialize style trainer. Disabled by default."""
        enabled = self.ctx.config.get("enabled", False)
        if not enabled:
            self._enabled = False
            logger.info(
                "StyleTrainerCapability disabled for %s (set enabled: true to activate)",
                self.ctx.identity_name,
            )
            return

        self._min_examples = self.ctx.config.get("min_examples", 20)

        # Set up profile storage path
        data_dir = Path(self.ctx.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._profile_path = data_dir / "style_profile.json"

        # Load existing profile from disk if available
        if self._profile_path.exists():
            try:
                self._profile = json.loads(self._profile_path.read_text(encoding="utf-8"))
                logger.info(
                    "StyleTrainerCapability loaded existing profile for %s",
                    self.ctx.identity_name,
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load style profile: %s", e)
                self._profile = None

        logger.info(
            "StyleTrainerCapability ready for %s (min_examples=%d, profile=%s)",
            self.ctx.identity_name,
            self._min_examples,
            "loaded" if self._profile else "none",
        )

    async def ingest_examples(self, emails: list[dict[str, str]]) -> Optional[dict[str, Any]]:
        """
        Ingest example emails and extract a writing style profile.

        Args:
            emails: List of dicts with at least a "body" key (and optionally
                    "subject", "to", "language").

        Returns:
            The extracted style profile dict, or None if extraction failed.
        """
        if not self._enabled:
            logger.warning("StyleTrainerCapability is disabled, cannot ingest")
            return None

        if len(emails) < self._min_examples:
            logger.warning(
                "Not enough examples (%d < %d minimum) for reliable style extraction",
                len(emails),
                self._min_examples,
            )

        # Format emails for the prompt
        formatted = []
        for i, email in enumerate(emails, 1):
            parts = [f"--- Email {i} ---"]
            if "subject" in email:
                parts.append(f"Subject: {email['subject']}")
            if "to" in email:
                parts.append(f"To: {email['to']}")
            if "language" in email:
                parts.append(f"Language: {email['language']}")
            parts.append(f"Body:\n{email['body']}")
            formatted.append("\n".join(parts))

        emails_text = "\n\n".join(formatted)

        prompt = STYLE_EXTRACTION_PROMPT.format(
            count=len(emails),
            emails=emails_text,
        )

        if not self.ctx.llm_pipeline:
            logger.error("StyleTrainerCapability: no LLM pipeline available")
            return None

        result = await self.ctx.llm_pipeline.chat(
            messages=[{"role": "user", "content": prompt}],
            complexity=STYLE_ANALYSIS_COMPLEXITY,
        )

        if not result or getattr(result, "blocked", False):
            logger.warning("Style extraction LLM call blocked or failed")
            return None

        # Parse the JSON response
        content = result.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            profile = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse style profile JSON: %s", e)
            return None

        self._profile = profile
        self._save_profile()
        logger.info(
            "Style profile extracted from %d emails for %s",
            len(emails),
            self.ctx.identity_name,
        )
        return profile

    def get_style_profile(self) -> Optional[dict[str, Any]]:
        """Return the current style profile, or None if not yet trained."""
        return self._profile

    def get_style_prompt(self) -> str:
        """
        Return a prompt snippet instructing the LLM to mimic the principal's style.

        Returns empty string if no profile is available.
        """
        if not self._profile:
            return ""

        p = self._profile
        tone = ", ".join(p.get("tone_markers", ["professional"]))
        formality = p.get("formality_by_context", {}).get("known_contacts", "professional")
        avg_length = p.get("avg_sentence_length", "15-20")
        greetings = _format_patterns(p.get("greeting_patterns", {}))
        closings = _format_patterns(p.get("closing_patterns", {}))
        vocabulary = ", ".join(p.get("vocabulary_preferences", [])[:10])
        avoided = ", ".join(p.get("vocabulary_avoided", [])[:10])

        extra_parts = []
        if p.get("language_switching"):
            extra_parts.append(f"- Language switching: {p['language_switching']}")
        if p.get("punctuation_habits"):
            extra_parts.append(f"- Punctuation: {p['punctuation_habits']}")
        if p.get("paragraph_structure"):
            extra_parts.append(f"- Paragraphs: {p['paragraph_structure']}")

        extra = "\n".join(extra_parts)

        return STYLE_PROMPT_TEMPLATE.format(
            tone=tone,
            formality=formality,
            avg_length=avg_length,
            greetings=greetings,
            closings=closings,
            vocabulary=vocabulary or "none specified",
            avoided=avoided or "none specified",
            extra=extra,
        ).strip()

    def get_prompt_context(self) -> str:
        """Return style context for automatic injection into LLM prompts."""
        return self.get_style_prompt()

    def _save_profile(self) -> None:
        """Persist the style profile to disk."""
        if self._profile and self._profile_path:
            try:
                self._profile_path.write_text(
                    json.dumps(self._profile, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.error("Failed to save style profile: %s", e)

    async def teardown(self) -> None:
        """Save profile on teardown."""
        if self._profile:
            self._save_profile()


def _format_patterns(patterns: dict[str, str]) -> str:
    """Format a dict of language→pattern into a readable string."""
    if not patterns:
        return "default"
    return ", ".join(f"{lang}: \"{pat}\"" for lang, pat in patterns.items())
