"""
Knowledge loader — injects YAML knowledge files into prompts.

Loads identity-specific knowledge files (knowledge_*.yaml) and
formats them for inclusion in LLM system prompts.
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class KnowledgeLoader:
    """
    Loads and formats knowledge from YAML files for prompt injection.
    """

    def __init__(self, identity_dir: Path):
        self._identity_dir = identity_dir
        self._knowledge: dict[str, list[str]] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all knowledge_*.yaml files from identity directory."""
        for path in sorted(self._identity_dir.glob("knowledge_*.yaml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)

                if isinstance(data, dict):
                    for category, items in data.items():
                        if isinstance(items, list):
                            self._knowledge.setdefault(category, []).extend(
                                str(item) for item in items
                            )
                        elif isinstance(items, str):
                            self._knowledge.setdefault(category, []).append(items)

                logger.debug("Loaded knowledge: %s (%d categories)", path.name, len(data or {}))

            except Exception as e:
                logger.warning("Could not load %s: %s", path, e)

    def get_knowledge(self, category: Optional[str] = None) -> list[str]:
        """Get knowledge items, optionally filtered by category."""
        if category:
            return self._knowledge.get(category, [])
        return [item for items in self._knowledge.values() for item in items]

    def format_for_prompt(self, max_items: int = 20) -> str:
        """Format knowledge for injection into system prompt."""
        if not self._knowledge:
            return ""

        parts = ["\n\nKNOWLEDGE BASE:"]
        count = 0
        for category, items in self._knowledge.items():
            parts.append(f"\n{category.upper()}:")
            for item in items:
                if count >= max_items:
                    break
                parts.append(f"  - {item}")
                count += 1

        return "\n".join(parts)

    @property
    def categories(self) -> list[str]:
        """Get all knowledge categories."""
        return list(self._knowledge.keys())

    @property
    def total_items(self) -> int:
        """Total number of knowledge items."""
        return sum(len(items) for items in self._knowledge.values())
