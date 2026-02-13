"""
SpeechToTextCapability — audio transcription (placeholder).

Defines the API surface for speech-to-text transcription.
Real audio processing backends will be integrated later.
"""

import logging
from collections.abc import AsyncIterator
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)


class SpeechToTextCapability(CapabilityBase):
    """
    Speech-to-text transcription capability.

    Placeholder implementation — all transcription methods return
    empty results and log a warning. Configure via identity YAML:

        capabilities:
          stt:
            model: whisper-large-v3
            language: en
            sample_rate: 16000
            beam_size: 5
    """

    name = "stt"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._model: str = "whisper-large-v3"
        self._language: str = "en"
        self._sample_rate: int = 16000
        self._beam_size: int = 5

    async def setup(self) -> None:
        self._model = self.ctx.config.get("model", "whisper-large-v3")
        self._language = self.ctx.config.get("language", "en")
        self._sample_rate = self.ctx.config.get("sample_rate", 16000)
        self._beam_size = self.ctx.config.get("beam_size", 5)
        logger.info(
            "SpeechToTextCapability initialized for %s (model=%s, lang=%s)",
            self.ctx.identity_name,
            self._model,
            self._language,
        )

    async def transcribe(
        self, audio_data: bytes, language: Optional[str] = None
    ) -> Optional[str]:
        """
        Transcribe audio data to text.

        Args:
            audio_data: Raw audio bytes.
            language: Optional language override (e.g. "en", "sv").

        Returns:
            Transcribed text, or None if not implemented.
        """
        logger.warning("SpeechToTextCapability.transcribe() not yet implemented")
        return None

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        """
        Stream-transcribe audio chunks to text segments.

        Args:
            audio_chunks: Async iterator of audio byte chunks.

        Yields:
            Transcribed text segments (currently yields nothing).
        """
        logger.warning(
            "SpeechToTextCapability.stream_transcribe() not yet implemented"
        )
        # Consume the iterator to prevent resource leaks
        async for _ in audio_chunks:
            pass
        # Yield nothing — placeholder
        return
        yield  # noqa: RET504 — makes this an async generator
