"""
Software engineering capability — STUB for future code generation.

This capability will eventually enable the agent to:
- Generate code patches
- Create pull requests
- Propose fixes for failing CI
- Suggest refactoring improvements

Currently a placeholder that reports itself as not ready.
"""

import logging

logger = logging.getLogger(__name__)


class SoftwareEngineeringCapability:
    """
    STUB: Future capability for code generation and PR creation.

    When implemented, this will use the LLM pipeline with code-specialized
    prompts to generate patches, create branches, and open PRs.
    """

    name = "software_engineering"

    def __init__(self, ctx):
        self.ctx = ctx
        self._ready = False

    async def setup(self) -> None:
        """Initialize (currently a no-op stub)."""
        logger.info(
            "SoftwareEngineeringCapability: STUB — not yet implemented. "
            "Code generation features will be available in a future release."
        )

    @property
    def configured(self) -> bool:
        """Whether the capability is ready to use."""
        return self._ready

    async def teardown(self) -> None:
        """Cleanup (no-op)."""
        pass
