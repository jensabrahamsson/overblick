from __future__ import annotations

from pathlib import Path
from typing import Any

from overblick.core.learning.store import LearningStore
from overblick.core.llm.client import LLMClient
from overblick.identities import Identity


class LearningStoreBuilder:
    def __init__(
        self,
        *,
        identity: Identity,
        llm_client: LLMClient | None,
        data_dir: Path,
    ) -> None:
        self._identity = identity
        self._llm_client = llm_client
        self._data_dir = data_dir

    async def build(self) -> LearningStore | None:
        """
        Build and initialize a LearningStore for the identity.

        Uses ethos text from identity, embedding function from LLM client if available.
        Returns None if learning is disabled or no embedding function can be resolved.
        """
        from logging import getLogger

        logger = getLogger(__name__)

        # Check if learning is enabled in identity config
        learning_config = getattr(self._identity, "learning", None)
        if learning_config is None:
            # Identity has no learning config — check raw_config fallback
            raw = getattr(self._identity, "raw_config", {}) or {}
            learning_raw = raw.get("learning", {})
            if not learning_raw or not learning_raw.get("enabled", False):
                logger.debug("Learning disabled for %s (no config)", self._identity.name)
                return None
            ethos_text = learning_raw.get("ethos", "")
        else:
            if not learning_config.enabled:
                return None
            ethos_text = learning_config.ethos

        # Try to get embedding function from LLM client
        embed_fn = None
        if self._llm_client:
            try:
                embed_fn = await self._llm_client.get_embedding_function()
            except Exception as e:
                logger.debug("Failed to get embedding function: %s", e)

        # Build store
        store = LearningStore(
            identity_name=self._identity.name,
            data_dir=self._data_dir,
            ethos_text=ethos_text,
            embed_fn=embed_fn,
        )
        await store.setup()
        return store
