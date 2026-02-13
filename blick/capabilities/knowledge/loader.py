"""
KnowledgeCapability — wraps KnowledgeLoader as a composable capability.

Loads YAML knowledge files and formats them for LLM prompt injection.
"""

import logging
from pathlib import Path
from typing import Optional

from blick.core.capability import CapabilityBase, CapabilityContext
from blick.plugins.moltbook.knowledge_loader import KnowledgeLoader

logger = logging.getLogger(__name__)


class KnowledgeCapability(CapabilityBase):
    """
    Knowledge loading capability.

    Wraps KnowledgeLoader. Loads identity-specific knowledge_*.yaml
    files at setup and provides formatted context for LLM prompts.
    """

    name = "knowledge_loader"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._loader: Optional[KnowledgeLoader] = None

    async def setup(self) -> None:
        knowledge_dir = self.ctx.config.get("knowledge_dir", None)
        if knowledge_dir:
            path = Path(knowledge_dir)
        else:
            # Default: look in identities/<name>/ directory
            path = Path(__file__).parent.parent.parent / "identities" / self.ctx.identity_name

        if path.exists():
            self._loader = KnowledgeLoader(path)
            logger.info(
                "KnowledgeCapability loaded %d items from %s",
                self._loader.total_items, path,
            )
        else:
            logger.debug("No knowledge directory found at %s", path)

    def get_prompt_context(self, max_items: int = 10) -> str:
        """Return formatted knowledge for injection into LLM prompts."""
        if not self._loader:
            return ""
        return self._loader.format_for_prompt(max_items=max_items)

    def get_knowledge(self, category: Optional[str] = None) -> list[str]:
        """Get knowledge items, optionally by category."""
        if not self._loader:
            return []
        return self._loader.get_knowledge(category)

    @property
    def categories(self) -> list[str]:
        """Get all knowledge categories."""
        if not self._loader:
            return []
        return self._loader.categories

    @property
    def inner(self) -> Optional[KnowledgeLoader]:
        """Access the underlying KnowledgeLoader (for tests/migration)."""
        return self._loader
