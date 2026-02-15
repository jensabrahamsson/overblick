"""Communication capabilities — email, notifications, messaging."""

from .boss_request import BossRequestCapability
from .email import EmailCapability
from .gmail import GmailCapability

__all__ = ["BossRequestCapability", "EmailCapability", "GmailCapability"]
