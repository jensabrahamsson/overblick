"""
Tests for EmailCapability — SMTP email sending.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from email.mime.multipart import MIMEMultipart

from overblick.capabilities.communication.email import EmailCapability


def make_ctx(**overrides):
    """Create a test capability context."""
    ctx = MagicMock()
    ctx.identity_name = overrides.get("identity_name", "test")
    
    # Mock get_secret
    secrets = overrides.get("secrets", {
        "smtp_server": "smtp.example.com",
        "smtp_port": "587",
        "smtp_login": "user@example.com",
        "smtp_password": "secret123",
        "smtp_from_email": "from@example.com",
    })
    
    def get_secret(key):
        if key in secrets:
            return secrets[key]
        raise KeyError(f"Secret not found: {key}")
    
    ctx.get_secret = MagicMock(side_effect=get_secret)
    
    # Mock audit_log
    ctx.audit_log = MagicMock()
    ctx.audit_log.log = MagicMock()
    
    return ctx


class TestEmailCapability:
    @pytest.mark.asyncio
    async def test_initialization(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        assert cap.name == "email"
        assert cap.ctx == ctx
        assert cap._smtp_config is None

    @pytest.mark.asyncio
    async def test_setup_success(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        assert cap._smtp_config is not None
        assert cap._smtp_config["server"] == "smtp.example.com"
        assert cap._smtp_config["port"] == 587
        assert cap._smtp_config["login"] == "user@example.com"
        assert cap._smtp_config["password"] == "secret123"
        assert cap._smtp_config["from_email"] == "from@example.com"

    @pytest.mark.asyncio
    async def test_setup_missing_secrets(self):
        ctx = make_ctx(secrets={})
        cap = EmailCapability(ctx)
        
        with pytest.raises(RuntimeError, match="Email capability requires SMTP secrets"):
            await cap.setup()

    @pytest.mark.asyncio
    async def test_send_without_setup(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        # Don't call setup
        
        result = await cap.send(
            to="test@example.com",
            subject="Test",
            body="Test body",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_send_plain_text_email(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        with patch.object(cap, "_send_smtp") as mock_smtp:
            result = await cap.send(
                to="recipient@example.com",
                subject="Test Subject",
                body="Test body content",
            )
            
            assert result is True
            mock_smtp.assert_called_once()
            
            # Verify audit log
            ctx.audit_log.log.assert_called_once()
            call_args = ctx.audit_log.log.call_args
            assert call_args[1]["action"] == "email_sent"
            assert call_args[1]["details"]["to"] == "recipient@example.com"
            assert call_args[1]["details"]["subject"] == "Test Subject"
            assert call_args[1]["details"]["html"] is False

    @pytest.mark.asyncio
    async def test_send_html_email(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        with patch.object(cap, "_send_smtp") as mock_smtp:
            result = await cap.send(
                to="recipient@example.com",
                subject="HTML Test",
                body="<h1>Test</h1>",
                html=True,
            )
            
            assert result is True
            assert ctx.audit_log.log.call_args[1]["details"]["html"] is True

    @pytest.mark.asyncio
    async def test_send_with_custom_from(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        with patch.object(cap, "_send_smtp") as mock_smtp:
            result = await cap.send(
                to="recipient@example.com",
                subject="Custom From",
                body="Test",
                from_email="custom@example.com",
            )
            
            assert result is True
            # Verify the message has custom from
            msg = mock_smtp.call_args[0][0]
            assert msg["From"] == "custom@example.com"

    @pytest.mark.asyncio
    async def test_send_failure(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        with patch.object(cap, "_send_smtp", side_effect=Exception("SMTP error")):
            result = await cap.send(
                to="recipient@example.com",
                subject="Test",
                body="Test",
            )
            
            assert result is False
            # Audit log should not be called on failure
            ctx.audit_log.log.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_smtp_starttls(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        
        # Port 587 uses STARTTLS
        msg = MIMEMultipart("alternative")
        msg["From"] = "from@example.com"
        msg["To"] = "to@example.com"
        msg["Subject"] = "Test"
        
        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_smtp = MagicMock()
            mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)
            
            cap._send_smtp(msg)
            
            mock_smtp_class.assert_called_once_with("smtp.example.com", 587, timeout=30)
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("user@example.com", "secret123")
            mock_smtp.send_message.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_send_smtp_ssl(self):
        ctx = make_ctx(secrets={
            "smtp_server": "smtp.example.com",
            "smtp_port": "465",  # SSL port
            "smtp_login": "user@example.com",
            "smtp_password": "secret123",
            "smtp_from_email": "from@example.com",
        })
        
        cap = EmailCapability(ctx)
        await cap.setup()
        
        msg = MIMEMultipart("alternative")
        msg["From"] = "from@example.com"
        msg["To"] = "to@example.com"
        msg["Subject"] = "Test"
        
        with patch("smtplib.SMTP_SSL") as mock_smtp_class:
            mock_smtp = MagicMock()
            mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)
            
            cap._send_smtp(msg)
            
            mock_smtp_class.assert_called_once_with("smtp.example.com", 465, timeout=30)
            mock_smtp.login.assert_called_once_with("user@example.com", "secret123")
            mock_smtp.send_message.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_teardown(self):
        ctx = make_ctx()
        cap = EmailCapability(ctx)
        await cap.setup()
        await cap.teardown()
        # No exception = success (teardown is a no-op)
