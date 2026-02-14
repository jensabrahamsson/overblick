# Vision Capabilities

## Overview

The **vision** bundle provides image analysis capabilities for agent plugins using the Anthropic Claude API. It enables agents to process images from URLs or base64-encoded data, generating text descriptions and analysis using Claude's vision models (Haiku, Sonnet, Opus).

This bundle enables multimodal interaction — agents that can see and understand images in conversations, posts, and media-rich environments.

## Capabilities

### VisionCapability

Provides image analysis using Claude's vision API. Downloads images from URLs or accepts base64-encoded data, sends them to the Anthropic Messages API with configurable prompts, and returns text descriptions. Supports all Claude vision models (Haiku for fast/cheap, Sonnet for balanced, Opus for deep analysis).

**Registry name:** `vision`

## Methods

### VisionCapability

```python
async def analyze_image_url(
    self,
    image_url: str,
    context: str = "",
) -> Optional[str]:
    """
    Download an image from URL and analyze it.

    Args:
        image_url: URL of the image to analyze.
        context: Optional context hint for the analysis prompt.

    Returns:
        Text description of the image, or None on failure.

    Example:
        description = await vision.analyze_image_url(
            "https://example.com/photo.jpg",
            context="This is a profile picture"
        )
    """

async def analyze_image_base64(
    self,
    base64_image: str,
    media_type: str = "image/jpeg",
    context: str = "",
) -> Optional[str]:
    """
    Analyze a base64-encoded image.

    Args:
        base64_image: Base64-encoded image data.
        media_type: MIME type (e.g. "image/jpeg", "image/png").
        context: Optional context hint for the analysis prompt.

    Returns:
        Text description of the image, or None on failure.

    Example:
        import base64
        image_data = open("photo.jpg", "rb").read()
        b64_data = base64.b64encode(image_data).decode("ascii")
        description = await vision.analyze_image_base64(
            b64_data,
            media_type="image/jpeg",
            context="Product photo"
        )
    """
```

Configuration options (set in identity YAML under `capabilities.vision`):
- `model` (str, default "claude-3-haiku-20240307") — Claude model for vision analysis
- `max_tokens` (int, default 150) — Maximum tokens in analysis response
- `timeout_seconds` (int, default 30) — HTTP request timeout
- `default_prompt` (str, optional) — Override default analysis prompt
- `context_prompt` (str, optional) — Override context-aware prompt template
- `api_key` (str, required) — Anthropic API key (or load from SecretsManager)

## Plugin Integration

Plugins access the VisionCapability through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class TelegramPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load vision bundle
        caps = registry.create_all(["vision"], self.ctx, configs={
            "vision": {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 200,
                "api_key": self.secrets.get("anthropic_api_key"),
            },
        })
        for cap in caps:
            await cap.setup()

        self.vision = caps[0]

    async def handle_photo_message(self, photo_url: str):
        # Analyze image
        description = await self.vision.analyze_image_url(
            photo_url,
            context="User shared this photo in chat",
        )

        if description:
            # Generate response based on image content
            response = await self.composer.compose_comment(
                post_title="Photo Analysis",
                post_content=description,
                agent_name="user",
                prompt_template="React to this image: {content}",
            )
            await self.send_message(response)
```

## Configuration

Configure the vision bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  vision:
    model: claude-3-haiku-20240307  # Fast and cheap
    max_tokens: 150
    timeout_seconds: 30
    default_prompt: |
      Describe this image briefly in 1-2 sentences.
      Focus on: what it shows, any text visible, and key details.
    api_key: ${ANTHROPIC_API_KEY}  # Load from environment
```

Or use a different model for deeper analysis:

```yaml
capabilities:
  vision:
    model: claude-3-sonnet-20240229  # Balanced quality/cost
    max_tokens: 300
```

Or load the entire bundle:

```yaml
capabilities:
  - vision  # Expands to: vision (single capability)
```

## Usage Examples

### Analyze Image from URL

```python
from overblick.capabilities.vision import VisionCapability
from overblick.core.capability import CapabilityContext

# Initialize vision capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "model": "claude-3-haiku-20240307",
        "max_tokens": 150,
        "api_key": os.environ["ANTHROPIC_API_KEY"],
    },
)

vision = VisionCapability(ctx)
await vision.setup()

# Analyze image from URL
description = await vision.analyze_image_url(
    "https://example.com/photo.jpg"
)

print(f"Image description: {description}")
# Output: "A mountain landscape at sunset with snow-capped peaks and orange sky."
```

### Analyze Image with Context

Context hints help Claude focus on specific aspects:

```python
# Profile picture analysis
description = await vision.analyze_image_url(
    "https://example.com/avatar.jpg",
    context="This is a user's profile picture",
)
# Output: "A professional headshot showing a person in business attire, smiling at the camera."

# Product photo analysis
description = await vision.analyze_image_url(
    "https://shop.com/product.jpg",
    context="This is a product listing photo",
)
# Output: "A blue ceramic mug with white handle, shown from the side on a white background."

# Screenshot analysis
description = await vision.analyze_image_url(
    "https://example.com/screenshot.png",
    context="This is a software screenshot",
)
# Output: "A code editor showing Python code with syntax highlighting, terminal pane at bottom."
```

### Analyze Base64-Encoded Image

```python
import base64

# Read local image
image_data = open("photo.jpg", "rb").read()
b64_data = base64.b64encode(image_data).decode("ascii")

# Analyze
description = await vision.analyze_image_base64(
    base64_image=b64_data,
    media_type="image/jpeg",
    context="User uploaded this photo",
)

print(f"Description: {description}")
```

### Supported Image Formats

The capability automatically detects media type from URL extension:

```python
# Media type mapping
_MEDIA_TYPES = {
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Examples:
# https://example.com/photo.png → image/png
# https://example.com/avatar.jpg → image/jpeg
# https://example.com/banner.webp → image/webp
```

For base64 data, specify media type explicitly:

```python
description = await vision.analyze_image_base64(
    base64_image=b64_data,
    media_type="image/png",  # Specify format
)
```

### Custom Prompts

Override default prompts for specific use cases:

```python
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={
        "model": "claude-3-haiku-20240307",
        "default_prompt": "Describe this image in detail, focusing on colors, composition, and mood.",
        "context_prompt": "Analyze this image in the context of: {context}. Provide detailed observations.",
        "api_key": os.environ["ANTHROPIC_API_KEY"],
    },
)
```

### Model Selection

Choose Claude model based on use case:

```python
# Fast and cheap (default)
config = {"model": "claude-3-haiku-20240307", "max_tokens": 150}
# Use for: Quick descriptions, simple image classification

# Balanced quality/cost
config = {"model": "claude-3-sonnet-20240229", "max_tokens": 300}
# Use for: Detailed analysis, nuanced understanding

# Highest quality
config = {"model": "claude-3-opus-20240229", "max_tokens": 500}
# Use for: Complex scenes, artistic analysis, dense text extraction
```

### Error Handling

```python
description = await vision.analyze_image_url("https://example.com/photo.jpg")

if description is None:
    # Possible causes:
    # - API key not configured
    # - Image download failed (404, network error)
    # - Claude API error (rate limit, invalid request)
    # - Timeout exceeded
    logger.warning("Image analysis failed")
else:
    print(f"Description: {description}")
```

### Integration with Conversation

```python
# User sends image in chat
async def handle_image_message(self, image_url: str, caption: str):
    # Analyze image
    description = await self.vision.analyze_image_url(
        image_url,
        context=f"User says: {caption}",
    )

    if not description:
        await self.send_message("Sorry, I couldn't analyze that image.")
        return

    # Add to conversation context
    self.conversation.add_user_message(
        chat_id,
        f"[Image: {description}] {caption}",
    )

    # Generate response
    messages = self.conversation.get_messages(chat_id, system_prompt=self.system_prompt)
    response = await self.llm_client.chat(messages=messages)

    self.conversation.add_assistant_message(chat_id, response["content"])
    await self.send_message(response["content"])
```

## Testing

Run vision capability tests:

```bash
# Test vision capability (requires API key)
ANTHROPIC_API_KEY=sk-... pytest tests/capabilities/test_vision.py -v
```

Example test pattern:

```python
import pytest
from overblick.capabilities.vision import VisionCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_vision_url_analysis(api_key):
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={
            "model": "claude-3-haiku-20240307",
            "api_key": api_key,
        },
    )

    vision = VisionCapability(ctx)
    await vision.setup()

    # Analyze test image
    description = await vision.analyze_image_url(
        "https://example.com/test-image.jpg"
    )

    assert description is not None
    assert len(description) > 0

@pytest.mark.asyncio
async def test_vision_base64_analysis(api_key):
    import base64

    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        config={"api_key": api_key},
    )

    vision = VisionCapability(ctx)
    await vision.setup()

    # Create test image (1x1 red pixel PNG)
    test_png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    ).decode("ascii")

    description = await vision.analyze_image_base64(
        base64_image=test_png,
        media_type="image/png",
    )

    assert description is not None
```

## Architecture

### VisionCapability Implementation

```python
class VisionCapability(CapabilityBase):
    name = "vision"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._model: str = "claude-3-haiku-20240307"
        self._max_tokens: int = 150
        self._timeout_seconds: int = 30
        self._default_prompt: str = _DEFAULT_PROMPT
        self._context_prompt: str = _CONTEXT_PROMPT
        self._api_key: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def setup(self) -> None:
        # Load config
        self._model = self.ctx.config.get("model", "claude-3-haiku-20240307")
        self._max_tokens = self.ctx.config.get("max_tokens", 150)
        self._api_key = self.ctx.config.get("api_key")

        if not self._api_key:
            logger.warning("VisionCapability has no API key — disabled")
            self._enabled = False
        else:
            logger.info(
                "VisionCapability initialized for %s (model=%s)",
                self.ctx.identity_name,
                self._model,
            )

    async def teardown(self) -> None:
        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
```

### API Request Format

Images are sent to Claude using the Messages API format:

```python
payload = {
    "model": "claude-3-haiku-20240307",
    "max_tokens": 150,
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "<base64-encoded-image>",
                    },
                },
                {
                    "type": "text",
                    "text": "Describe this image briefly...",
                },
            ],
        }
    ],
}

# Send to https://api.anthropic.com/v1/messages
# Headers:
#   x-api-key: <api-key>
#   anthropic-version: 2023-06-01
#   Content-Type: application/json
```

### HTTP Session Management

The capability maintains a single `aiohttp.ClientSession` for efficient connection pooling:

```python
async def _ensure_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
        self._session = aiohttp.ClientSession(
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
        )
    return self._session
```

Session is closed in `teardown()`.

### Default Prompts

```python
_DEFAULT_PROMPT = (
    "Describe this image briefly in 1-2 sentences. "
    "Focus on: what it shows, any text visible, and key details."
)

_CONTEXT_PROMPT = (
    "Describe this image briefly (1-2 sentences). Context: {context}"
)
```

These can be overridden in config for custom analysis styles.

### Cost Optimization

Use Haiku for most vision tasks (cheapest):

```python
# Cost comparison (approximate):
# Haiku:  $0.25 per 1M input tokens
# Sonnet: $3.00 per 1M input tokens
# Opus:   $15.00 per 1M input tokens

# Image ~= 1000-2000 tokens depending on resolution
# Haiku is 12x cheaper than Sonnet, 60x cheaper than Opus
```

Only use Sonnet/Opus when image understanding quality matters significantly.

## Related Bundles

- **conversation** — Include image descriptions in conversation history
- **engagement** — Generate responses based on image content
- **speech** — Multimodal interaction (vision + audio)
- **content** — Summarize image descriptions before analysis
