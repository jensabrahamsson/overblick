"""
Input sanitizer — clean external data before processing.

Strips null bytes, normalizes unicode, truncates excessive length,
and removes control characters from untrusted input.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# Max input length (characters)
MAX_INPUT_LENGTH = 10_000

# Control characters to strip (except newline, tab, carriage return)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: str, max_length: int = MAX_INPUT_LENGTH) -> str:
    """
    Sanitize external input text.

    Applies:
    1. Null byte removal
    2. Control character stripping
    3. Unicode NFC normalization
    4. Length truncation

    Args:
        text: Raw input text
        max_length: Maximum allowed length

    Returns:
        Sanitized text
    """
    if not text:
        return ""

    # Remove null bytes (PostgreSQL/SQLite reject these)
    result = text.replace("\x00", "")

    # Strip control characters (keep \n, \t, \r)
    result = _CONTROL_CHAR_RE.sub("", result)

    # Normalize unicode (NFC — canonical composition)
    result = unicodedata.normalize("NFC", result)

    # Truncate
    if len(result) > max_length:
        result = result[:max_length]
        logger.debug(f"Input truncated from {len(text)} to {max_length} chars")

    return result


def sanitize_dict(data: dict, max_length: int = MAX_INPUT_LENGTH) -> dict:
    """
    Sanitize all string values in a dict (non-recursive).

    Args:
        data: Dict with potentially unsafe string values
        max_length: Maximum length per value

    Returns:
        New dict with sanitized string values
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = sanitize(value, max_length)
        else:
            result[key] = value
    return result
