"""Communication capabilities — email, notifications, messaging."""

from .email import EmailCapability
from .gmail import GmailCapability

__all__ = ["EmailCapability", "GmailCapability"]
