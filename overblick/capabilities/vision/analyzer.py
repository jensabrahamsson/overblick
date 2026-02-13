"""
VisionCapability — image analysis via the Anthropic Claude API.

Provides image description and analysis using Claude's vision models.
Images are base64-encoded and sent to the Anthropic Messages API.

API key is loaded from SecretsManager (key: "anthropic_api_key").
Model defaults to claude-3-haiku (cheapest vision-capable model).

Usage:
    cap = VisionCapability(ctx)
    await cap.setup()
    description = await cap.analyze_image_url("https://example.com/photo.jpg")
    description = await cap.analyze_image_base64(b64_data, "image/png")
"""

import base64
import logging
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from overblick.core.capability import CapabilityBase, CapabilityContext

logger = logging.getLogger(__name__)

# Anthropic API endpoint
_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

# Default prompts
_DEFAULT_PROMPT = (
    "Describe this image briefly in 1-2 sentences. "
    "Focus on: what it shows, any text visible, and key details."
)
_CONTEXT_PROMPT = (
    "Describe this image briefly (1-2 sentences). Context: {context}"
)

# Media type mapping by extension
_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_DEFAULT_MEDIA_TYPE = "image/jpeg"


class VisionCapability(CapabilityBase):
    """
    Image analysis capability using Claude's vision API.

    Configure via identity YAML:

        capabilities:
          vision:
            model: claude-3-haiku-20240307
            max_tokens: 150
            timeout_seconds: 30
            default_prompt: "Describe this image briefly..."
    """

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
        self._model = self.ctx.config.get("model", "claude-3-haiku-20240307")
        self._max_tokens = self.ctx.config.get("max_tokens", 150)
        self._timeout_seconds = self.ctx.config.get("timeout_seconds", 30)
        self._default_prompt = self.ctx.config.get("default_prompt", _DEFAULT_PROMPT)
        self._context_prompt = self.ctx.config.get("context_prompt", _CONTEXT_PROMPT)
        self._api_key = self.ctx.config.get("api_key")

        if not self._api_key:
            logger.warning(
                "VisionCapability has no API key — set 'anthropic_api_key' in secrets "
                "or pass 'api_key' in capability config"
            )
            self._enabled = False
        else:
            logger.info(
                "VisionCapability initialized for %s (model=%s)",
                self.ctx.identity_name,
                self._model,
            )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Create or return the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "x-api-key": self._api_key or "",
                    "anthropic-version": _API_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self._timeout_seconds),
            )
        return self._session

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
        """
        if not self._enabled:
            logger.warning("VisionCapability is disabled (no API key)")
            return None

        try:
            session = await self._ensure_session()
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Failed to download image from %s: HTTP %d",
                        image_url,
                        resp.status,
                    )
                    return None
                image_data = await resp.read()
        except Exception as e:
            logger.error("Error downloading image from %s: %s", image_url, e)
            return None

        # Detect media type from URL extension
        path = urlparse(image_url).path.lower()
        media_type = _DEFAULT_MEDIA_TYPE
        for ext, mt in _MEDIA_TYPES.items():
            if path.endswith(ext):
                media_type = mt
                break

        b64_data = base64.b64encode(image_data).decode("ascii")
        return await self.analyze_image_base64(b64_data, media_type, context)

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
        """
        if not self._enabled:
            logger.warning("VisionCapability is disabled (no API key)")
            return None

        prompt = (
            self._context_prompt.format(context=context)
            if context
            else self._default_prompt
        )

        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        }

        try:
            session = await self._ensure_session()
            async with session.post(_API_URL, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        "Claude API error: HTTP %d — %s", resp.status, body[:200]
                    )
                    return None

                data = await resp.json()
                content = data.get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0]["text"]

                logger.warning("Unexpected Claude API response format: %s", data)
                return None

        except aiohttp.ClientError as e:
            logger.error("Claude API request failed: %s", e)
            return None
        except Exception as e:
            logger.error("Unexpected error in vision analysis: %s", e)
            return None

    async def teardown(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        logger.debug("VisionCapability session closed")
