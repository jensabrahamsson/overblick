"""Tests for dashboard services __init__.py (init_services and cleanup_services)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.dashboard.config import DashboardConfig
from overblick.dashboard.services import init_services, cleanup_services


class TestInitServices:
    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.state = MagicMock()
        return app

    @pytest.fixture
    def config_with_base_dir(self, tmp_path):
        return DashboardConfig(
            base_dir=str(tmp_path),
            secret_key="test-key",
        )

    @pytest.fixture
    def config_without_base_dir(self):
        return DashboardConfig(
            base_dir="",
            secret_key="test-key",
        )

    @pytest.mark.asyncio
    async def test_should_init_services_with_base_dir(self, mock_app, config_with_base_dir, tmp_path):
        with patch("overblick.dashboard.services.audit.AuditService") as mock_audit, \
             patch("overblick.dashboard.services.identity.IdentityService") as mock_identity, \
             patch("overblick.dashboard.services.personality.PersonalityService") as mock_personality, \
             patch("overblick.dashboard.services.supervisor.SupervisorService") as mock_supervisor, \
             patch("overblick.dashboard.services.system.SystemService") as mock_system, \
             patch("overblick.dashboard.services.irc.IRCService") as mock_irc, \
             patch("overblick.dashboard.services.onboarding.OnboardingService") as mock_onboarding, \
             patch("overblick.dashboard.services.secrets.SecretsService") as mock_secrets:
            await init_services(mock_app, config_with_base_dir)

        mock_identity.assert_called_once_with(tmp_path)
        mock_personality.assert_called_once_with()
        mock_audit.assert_called_once_with(tmp_path)
        mock_supervisor.assert_called_once_with(socket_dir=tmp_path / "data" / "ipc")
        mock_system.assert_called_once_with(tmp_path)
        mock_irc.assert_called_once_with(tmp_path)
        mock_onboarding.assert_called_once_with(tmp_path)
        mock_secrets.assert_called_once_with(tmp_path)

    @pytest.mark.asyncio
    async def test_should_init_services_without_base_dir(self, mock_app, config_without_base_dir):
        with patch("overblick.dashboard.services.audit.AuditService"), \
             patch("overblick.dashboard.services.identity.IdentityService"), \
             patch("overblick.dashboard.services.personality.PersonalityService"), \
             patch("overblick.dashboard.services.supervisor.SupervisorService"), \
             patch("overblick.dashboard.services.system.SystemService"), \
             patch("overblick.dashboard.services.irc.IRCService"), \
             patch("overblick.dashboard.services.onboarding.OnboardingService"), \
             patch("overblick.dashboard.services.secrets.SecretsService"):
            await init_services(mock_app, config_without_base_dir)

        # Verify services were set on app state (all should be mock instances)
        assert mock_app.state.identity_service is not None
        assert mock_app.state.personality_service is not None
        assert mock_app.state.audit_service is not None
        assert mock_app.state.supervisor_service is not None
        assert mock_app.state.system_service is not None
        assert mock_app.state.irc_service is not None
        assert mock_app.state.onboarding_service is not None
        assert mock_app.state.secrets_service is not None


class TestCleanupServices:
    @pytest.mark.asyncio
    async def test_should_cleanup_audit_and_supervisor(self):
        app = MagicMock()
        app.state.audit_service = MagicMock()
        app.state.supervisor_service = AsyncMock()

        await cleanup_services(app)

        app.state.audit_service.close.assert_called_once()
        app.state.supervisor_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_missing_audit_service(self):
        app = MagicMock(spec=[])
        app.state = MagicMock(spec=[])

        # Remove the attributes so hasattr returns False
        del app.state.audit_service
        del app.state.supervisor_service

        await cleanup_services(app)  # Should not raise

    @pytest.mark.asyncio
    async def test_should_cleanup_supervisor_even_without_audit(self):
        app = MagicMock()
        # Only supervisor is present
        del app.state.audit_service
        app.state.supervisor_service = AsyncMock()

        # hasattr should return False for audit_service
        original_hasattr = hasattr

        await cleanup_services(app)
        app.state.supervisor_service.close.assert_called_once()
