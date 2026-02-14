# Speech Capabilities

## Overview

The **speech** bundle provides speech-to-text (STT) and text-to-speech (TTS) capabilities for agent plugins. Currently placeholder implementations with defined API surfaces, these capabilities establish the interface for future audio processing backends (Whisper for STT, Piper/Coqui for TTS).

This bundle enables multimodal interaction — agents that can listen to voice messages and respond with synthesized speech.

## Capabilities

### SpeechToTextCapability

Defines the API surface for audio transcription. Placeholder implementation logs warnings and returns empty results. Real audio processing backends (Whisper, Vosk, etc.) will be integrated later.

**Registry name:** `stt`

### TextToSpeechCapability

Defines the API surface for text-to-speech synthesis. Placeholder implementation logs warnings and returns empty results. Real synthesis backends (Piper, Coqui TTS, etc.) will be integrated later.

**Registry name:** `tts`

## Methods

### SpeechToTextCapability

```python
async def transcribe(
    self,
    audio_data: bytes,
    language: Optional[str] = None,
) -> Optional[str]:
    """
    Transcribe audio data to text.

    Args:
        audio_data: Raw audio bytes.
        language: Optional language override (e.g. "en", "sv").

    Returns:
        Transcribed text, or None if not implemented.

    Note:
        Currently a placeholder — logs warning and returns None.
    """

async def stream_transcribe(
    self,
    audio_chunks: AsyncIterator[bytes],
) -> AsyncIterator[str]:
    """
    Stream-transcribe audio chunks to text segments.

    Args:
        audio_chunks: Async iterator of audio byte chunks.

    Yields:
        Transcribed text segments (currently yields nothing).

    Note:
        Currently a placeholder — consumes iterator and yields nothing.
    """
```

Configuration options (set in identity YAML under `capabilities.stt`):
- `model` (str, default "whisper-large-v3") — STT model identifier
- `language` (str, default "en") — Default language for transcription
- `sample_rate` (int, default 16000) — Audio sample rate in Hz
- `beam_size` (int, default 5) — Beam search size for transcription quality

### TextToSpeechCapability

```python
async def synthesize(
    self,
    text: str,
    voice: Optional[str] = None,
) -> Optional[bytes]:
    """
    Synthesize text into audio bytes.

    Args:
        text: Text to synthesize.
        voice: Optional voice override.

    Returns:
        Audio bytes, or None if not implemented.

    Note:
        Currently a placeholder — logs warning and returns None.
    """

async def get_voices(self) -> list[str]:
    """
    List available voice identifiers.

    Returns:
        List of voice names (currently empty).

    Note:
        Currently a placeholder — returns empty list.
    """
```

Configuration options (set in identity YAML under `capabilities.tts`):
- `model` (str, default "piper-tts") — TTS model/engine identifier
- `voice` (str, default "default") — Default voice identifier
- `speed` (float, default 1.0) — Speech speed multiplier
- `output_format` (str, default "wav") — Audio output format

## Plugin Integration

Plugins access speech capabilities through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class TelegramPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load speech bundle (stt, tts)
        caps = registry.create_all(["speech"], self.ctx, configs={
            "stt": {
                "model": "whisper-large-v3",
                "language": "en",
                "sample_rate": 16000,
            },
            "tts": {
                "model": "piper-tts",
                "voice": "cherry-voice",
                "speed": 1.0,
            },
        })
        for cap in caps:
            await cap.setup()

        self.stt = caps[0]
        self.tts = caps[1]

    async def handle_voice_message(self, audio_bytes: bytes):
        # Transcribe voice message
        text = await self.stt.transcribe(audio_bytes, language="en")
        if not text:
            logger.warning("Transcription failed (not yet implemented)")
            return

        # Process text
        response_text = await self.process_message(text)

        # Synthesize voice response
        response_audio = await self.tts.synthesize(response_text, voice="cherry-voice")
        if not response_audio:
            logger.warning("TTS failed (not yet implemented)")
            return

        # Send voice message back
        await self.send_voice_message(response_audio)
```

## Configuration

Configure the speech bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  stt:
    model: whisper-large-v3
    language: en
    sample_rate: 16000
    beam_size: 5

  tts:
    model: piper-tts
    voice: cherry-voice
    speed: 1.0
    output_format: wav
```

Or load the entire bundle:

```yaml
capabilities:
  - speech  # Expands to: stt, tts
```

## Usage Examples

### Speech-to-Text (Placeholder)

```python
from overblick.capabilities.speech import SpeechToTextCapability
from overblick.core.capability import CapabilityContext

# Initialize STT capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "model": "whisper-large-v3",
        "language": "en",
        "sample_rate": 16000,
    },
)

stt = SpeechToTextCapability(ctx)
await stt.setup()

# Transcribe audio (currently logs warning and returns None)
audio_data = open("voice_message.wav", "rb").read()
text = await stt.transcribe(audio_data, language="en")

if text:
    print(f"Transcription: {text}")
else:
    print("STT not yet implemented")
```

### Streaming Transcription (Placeholder)

```python
# Stream audio chunks (e.g., from live microphone)
async def audio_stream():
    # Simulated audio chunks
    for i in range(10):
        chunk = await read_audio_chunk()
        yield chunk

# Stream transcription
async for text_segment in stt.stream_transcribe(audio_stream()):
    print(f"Partial: {text_segment}")
    # Currently yields nothing (placeholder)
```

### Text-to-Speech (Placeholder)

```python
from overblick.capabilities.speech import TextToSpeechCapability

# Initialize TTS capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "model": "piper-tts",
        "voice": "cherry-voice",
        "speed": 1.0,
    },
)

tts = TextToSpeechCapability(ctx)
await tts.setup()

# Synthesize speech (currently logs warning and returns None)
text = "Hello, I'm Cherry. How can I help you?"
audio_bytes = await tts.synthesize(text, voice="cherry-voice")

if audio_bytes:
    # Save or stream audio
    with open("response.wav", "wb") as f:
        f.write(audio_bytes)
else:
    print("TTS not yet implemented")
```

### List Available Voices (Placeholder)

```python
# Get available voices
voices = await tts.get_voices()
print(f"Available voices: {voices}")
# Currently returns: []
```

### Future Implementation Pattern

When real backends are integrated, usage will remain the same:

```python
# STT with Whisper backend (future)
stt = SpeechToTextCapability(ctx)
await stt.setup()  # Loads Whisper model

text = await stt.transcribe(audio_data)  # Real transcription
# Returns: "Hello, I'm interested in AI consciousness."

# TTS with Piper backend (future)
tts = TextToSpeechCapability(ctx)
await tts.setup()  # Loads Piper voice model

audio = await tts.synthesize("That's a fascinating topic!")
# Returns: bytes (WAV audio data)
```

## Testing

Run speech capability tests:

```bash
# Test placeholder implementations
pytest tests/capabilities/test_speech.py -v
```

Example test pattern:

```python
import pytest
from overblick.capabilities.speech import SpeechToTextCapability, TextToSpeechCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_stt_placeholder():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={"model": "whisper-large-v3"},
    )

    stt = SpeechToTextCapability(ctx)
    await stt.setup()

    # Placeholder returns None
    result = await stt.transcribe(b"fake audio data")
    assert result is None

@pytest.mark.asyncio
async def test_tts_placeholder():
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={"model": "piper-tts"},
    )

    tts = TextToSpeechCapability(ctx)
    await tts.setup()

    # Placeholder returns None
    result = await tts.synthesize("Hello world")
    assert result is None

    # Placeholder returns empty list
    voices = await tts.get_voices()
    assert voices == []
```

## Architecture

### SpeechToTextCapability

```python
class SpeechToTextCapability(CapabilityBase):
    name = "stt"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._model: str = "whisper-large-v3"
        self._language: str = "en"
        self._sample_rate: int = 16000
        self._beam_size: int = 5

    async def setup(self) -> None:
        # Load config
        self._model = self.ctx.config.get("model", "whisper-large-v3")
        self._language = self.ctx.config.get("language", "en")
        # ...
        logger.info(
            "SpeechToTextCapability initialized for %s (model=%s, lang=%s)",
            self.ctx.identity_name,
            self._model,
            self._language,
        )

    async def transcribe(self, audio_data: bytes, language: Optional[str] = None) -> Optional[str]:
        logger.warning("SpeechToTextCapability.transcribe() not yet implemented")
        return None

    async def stream_transcribe(self, audio_chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
        logger.warning("SpeechToTextCapability.stream_transcribe() not yet implemented")
        async for _ in audio_chunks:
            pass  # Consume iterator to prevent resource leaks
        return
        yield  # Makes this an async generator
```

### TextToSpeechCapability

```python
class TextToSpeechCapability(CapabilityBase):
    name = "tts"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._model: str = "piper-tts"
        self._voice: str = "default"
        self._speed: float = 1.0
        self._output_format: str = "wav"

    async def setup(self) -> None:
        # Load config
        self._model = self.ctx.config.get("model", "piper-tts")
        self._voice = self.ctx.config.get("voice", "default")
        # ...
        logger.info(
            "TextToSpeechCapability initialized for %s (model=%s, voice=%s)",
            self.ctx.identity_name,
            self._model,
            self._voice,
        )

    async def synthesize(self, text: str, voice: Optional[str] = None) -> Optional[bytes]:
        logger.warning("TextToSpeechCapability.synthesize() not yet implemented")
        return None

    async def get_voices(self) -> list[str]:
        logger.warning("TextToSpeechCapability.get_voices() not yet implemented")
        return []
```

### Why Placeholders?

These capabilities define stable API surfaces before backend implementation:

1. **API-First Design:** Plugins can be written against the interface now
2. **Future-Proof:** When backends are added, no plugin code changes needed
3. **Gradual Integration:** STT and TTS can be implemented independently
4. **Testing:** Mock implementations can test plugin integration logic

### Future Backend Options

**Speech-to-Text:**
- **Whisper** — OpenAI's state-of-the-art STT (local inference via faster-whisper)
- **Vosk** — Offline STT with multiple languages
- **Cloud APIs** — Google Speech-to-Text, Azure Speech, AWS Transcribe

**Text-to-Speech:**
- **Piper** — Fast neural TTS (ONNX-based)
- **Coqui TTS** — High-quality open-source TTS
- **Bark** — Generative TTS with voice cloning
- **Cloud APIs** — ElevenLabs, Google Cloud TTS, Azure Speech

### Integration Pattern (Future)

When backends are implemented, they'll follow this pattern:

```python
# In SpeechToTextCapability.setup():
if self._model.startswith("whisper"):
    from faster_whisper import WhisperModel
    self._whisper = WhisperModel(self._model, device="cuda")

# In transcribe():
if self._whisper:
    segments, info = self._whisper.transcribe(audio_data, language=language)
    return " ".join([s.text for s in segments])
```

No changes to the capability API — just internal implementation.

## Related Bundles

- **conversation** — Transcribe voice messages into conversation history
- **engagement** — Generate responses, then synthesize to audio
- **vision** — Multimodal interaction (image + audio input)
