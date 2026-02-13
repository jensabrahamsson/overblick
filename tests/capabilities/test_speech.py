"""
Tests for SpeechToTextCapability and TextToSpeechCapability — placeholder speech capabilities.
"""

import pytest
from pathlib import Path

from overblick.core.capability import CapabilityContext
from overblick.capabilities.speech.stt import SpeechToTextCapability
from overblick.capabilities.speech.tts import TextToSpeechCapability


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


# ── STT ──────────────────────────────────────────────────────────────


class TestSpeechToTextCapability:
    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = SpeechToTextCapability(ctx)
        assert cap.name == "stt"

    @pytest.mark.asyncio
    async def test_setup_defaults(self):
        ctx = make_ctx()
        cap = SpeechToTextCapability(ctx)
        await cap.setup()
        assert cap._model == "whisper-large-v3"
        assert cap._language == "en"
        assert cap._sample_rate == 16000
        assert cap._beam_size == 5

    @pytest.mark.asyncio
    async def test_setup_custom_config(self):
        ctx = make_ctx(config={
            "model": "whisper-tiny",
            "language": "sv",
            "sample_rate": 8000,
            "beam_size": 3,
        })
        cap = SpeechToTextCapability(ctx)
        await cap.setup()
        assert cap._model == "whisper-tiny"
        assert cap._language == "sv"
        assert cap._sample_rate == 8000
        assert cap._beam_size == 3

    @pytest.mark.asyncio
    async def test_transcribe_returns_none(self):
        ctx = make_ctx()
        cap = SpeechToTextCapability(ctx)
        await cap.setup()
        result = await cap.transcribe(b"\x00\x01\x02")
        assert result is None

    @pytest.mark.asyncio
    async def test_transcribe_with_language_override(self):
        ctx = make_ctx()
        cap = SpeechToTextCapability(ctx)
        await cap.setup()
        result = await cap.transcribe(b"\x00\x01\x02", language="sv")
        assert result is None

    @pytest.mark.asyncio
    async def test_stream_transcribe_yields_nothing(self):
        ctx = make_ctx()
        cap = SpeechToTextCapability(ctx)
        await cap.setup()

        async def audio_chunks():
            yield b"\x00\x01"
            yield b"\x02\x03"

        segments = [s async for s in cap.stream_transcribe(audio_chunks())]
        assert segments == []


# ── TTS ──────────────────────────────────────────────────────────────


class TestTextToSpeechCapability:
    @pytest.mark.asyncio
    async def test_name(self):
        ctx = make_ctx()
        cap = TextToSpeechCapability(ctx)
        assert cap.name == "tts"

    @pytest.mark.asyncio
    async def test_setup_defaults(self):
        ctx = make_ctx()
        cap = TextToSpeechCapability(ctx)
        await cap.setup()
        assert cap._model == "piper-tts"
        assert cap._voice == "default"
        assert cap._speed == 1.0
        assert cap._output_format == "wav"

    @pytest.mark.asyncio
    async def test_setup_custom_config(self):
        ctx = make_ctx(config={
            "model": "coqui-tts",
            "voice": "sven",
            "speed": 1.5,
            "output_format": "mp3",
        })
        cap = TextToSpeechCapability(ctx)
        await cap.setup()
        assert cap._model == "coqui-tts"
        assert cap._voice == "sven"
        assert cap._speed == 1.5
        assert cap._output_format == "mp3"

    @pytest.mark.asyncio
    async def test_synthesize_returns_none(self):
        ctx = make_ctx()
        cap = TextToSpeechCapability(ctx)
        await cap.setup()
        result = await cap.synthesize("Hello world")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthesize_with_voice_override(self):
        ctx = make_ctx()
        cap = TextToSpeechCapability(ctx)
        await cap.setup()
        result = await cap.synthesize("Hello world", voice="custom")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_voices_returns_empty_list(self):
        ctx = make_ctx()
        cap = TextToSpeechCapability(ctx)
        await cap.setup()
        voices = await cap.get_voices()
        assert voices == []
