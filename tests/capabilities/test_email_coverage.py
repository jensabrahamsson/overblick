"""Additional tests for EmailCapability — cover lines 71, 74-75.

Uncovered: 71 (smtp_port missing/empty), 74-75 (smtp_port non-integer).
"""

from unittest.mock import MagicMock

import pytest

from overblick.capabilities.communication.email import EmailCapability


def make_ctx(**overrides):
    ctx = MagicMock()
    ctx.identity_name = overrides.get("identity_name", "test")
    secrets = overrides.get("secrets", {})

    def get_secret(key):
        if key in secrets:
            return secrets[key]
        raise KeyError(f"Secret not found: {key}")

    ctx.get_secret = MagicMock(side_effect=get_secret)
    ctx.audit_log = MagicMock()
    ctx.audit_log.log = MagicMock()
    return ctx


class TestEmailCoverage:
    @pytest.mark.asyncio
    async def test_setup_missing_smtp_port(self):
        """Cover line 71: smtp_port is None/empty."""
        ctx = make_ctx(secrets={
            "smtp_server": "smtp.example.com",
            "smtp_port": "",
            "smtp_login": "user@example.com",
            "smtp_password": "secret",
            "smtp_from_email": "from@example.com",
        })
        cap = EmailCapability(ctx)

        with pytest.raises(RuntimeError, match="Email capability requires SMTP secrets"):
            await cap.setup()

    @pytest.mark.asyncio
    async def test_setup_non_integer_smtp_port(self):
        """Cover lines 74-75: smtp_port is not a valid integer."""
        ctx = make_ctx(secrets={
            "smtp_server": "smtp.example.com",
            "smtp_port": "not_a_number",
            "smtp_login": "user@example.com",
            "smtp_password": "secret",
            "smtp_from_email": "from@example.com",
        })
        cap = EmailCapability(ctx)

        with pytest.raises(RuntimeError, match="Email capability requires SMTP secrets"):
            await cap.setup()
