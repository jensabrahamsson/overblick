"""
TextToSpeechCapability — text-to-speech synthesis (placeholder).

Defines the API surface for text-to-speech synthesis.
Real audio synthesis backends will be integrated later.
"""

import logging
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class TextToSpeechCapability(CapabilityBase):
    """
    Text-to-speech synthesis capability.

    Placeholder implementation — all synthesis methods return
    empty results and log a warning. Configure via identity YAML:

        capabilities:
          tts:
            model: piper-tts
            voice: default
            speed: 1.0
            output_format: wav
    """

    name = "tts"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._model: str = "piper-tts"
        self._voice: str = "default"
        self._speed: float = 1.0
        self._output_format: str = "wav"

    async def setup(self) -> None:
        self._model = self.ctx.config.get("model", "piper-tts")
        self._voice = self.ctx.config.get("voice", "default")
        self._speed = self.ctx.config.get("speed", 1.0)
        self._output_format = self.ctx.config.get("output_format", "wav")
        logger.info(
            "TextToSpeechCapability initialized for %s (model=%s, voice=%s)",
            self.ctx.identity_name,
            self._model,
            self._voice,
        )

    async def synthesize(
        self, text: str, voice: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Synthesize text into audio bytes.

        Args:
            text: Text to synthesize.
            voice: Optional voice override.

        Returns:
            Audio bytes, or None if not implemented.
        """
        logger.warning("TextToSpeechCapability.synthesize() not yet implemented")
        return None

    async def get_voices(self) -> list[str]:
        """
        List available voice identifiers.

        Returns:
            List of voice names (currently empty).
        """
        logger.warning("TextToSpeechCapability.get_voices() not yet implemented")
        return []
