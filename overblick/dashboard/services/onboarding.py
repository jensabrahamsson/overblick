"""
Onboarding service — create new identities.

This is the ONLY write-capable service in the dashboard.
It creates identity YAML files and stores secrets via SecretsManager.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class OnboardingService:
    """Create new identities through the onboarding wizard."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._identities_dir = base_dir / "overblick" / "identities"
        self._secrets_dir = base_dir / "config" / "secrets"

    def identity_exists(self, name: str) -> bool:
        """Check if an identity already exists."""
        return (self._identities_dir / name / "identity.yaml").exists()

    def create_identity(self, wizard_state: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new identity from wizard state.

        Args:
            wizard_state: Accumulated wizard data from all steps

        Returns:
            Result dict with created files list

        Raises:
            ValueError: If identity already exists or validation fails
        """
        name = wizard_state.get("name", "")
        if not name:
            raise ValueError("Identity name is required")
        if self.identity_exists(name):
            raise ValueError(f"Identity '{name}' already exists")

        # Create identity directory
        identity_dir = self._identities_dir / name
        identity_dir.mkdir(parents=True, exist_ok=True)

        # Build identity.yaml
        identity_config = self._build_identity_config(wizard_state)
        identity_path = identity_dir / "identity.yaml"
        with open(identity_path, "w") as f:
            yaml.safe_dump(identity_config, f, default_flow_style=False, sort_keys=False)

        created_files = [str(identity_path)]

        # Create data directory for the identity
        data_dir = self._base_dir / "data" / name
        data_dir.mkdir(parents=True, exist_ok=True)

        # Store secrets (if any)
        secrets = wizard_state.get("secrets", {})
        if secrets:
            try:
                from overblick.core.security.secrets_manager import SecretsManager
                sm = SecretsManager(self._secrets_dir)
                for key, value in secrets.items():
                    if value:  # Only store non-empty values
                        sm.set(name, key, value)
                created_files.append(f"secrets/{name}.yaml (encrypted)")
            except Exception as e:
                logger.error("Failed to store secrets for '%s': %s", name, e)

        logger.info("Created identity '%s' with files: %s", name, created_files)

        return {
            "name": name,
            "created_files": created_files,
            "identity_dir": str(identity_dir),
            "data_dir": str(data_dir),
        }

    def _build_identity_config(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build identity.yaml content from wizard state."""
        config: dict[str, Any] = {
            "name": state["name"],
            "display_name": state.get("display_name", state["name"].capitalize()),
            "description": state.get("description", ""),
            "version": "1.0.0",
        }

        # LLM settings
        llm = state.get("llm", {})
        if llm:
            config["llm"] = {
                "model": llm.get("model", "qwen3:8b"),
                "temperature": llm.get("temperature", 0.7),
                "max_tokens": llm.get("max_tokens", 2000),
                "use_gateway": llm.get("use_gateway", False),
            }

        # Personality reference
        personality = state.get("personality", "")
        if personality:
            config["personality"] = personality

        # Plugins
        plugins = state.get("plugins", [])
        if plugins:
            config["plugins"] = plugins

        # Capabilities
        capabilities = state.get("capabilities", [])
        if capabilities:
            config["capabilities"] = capabilities

        # Default schedule and security
        config["schedule"] = {"heartbeat_hours": 4, "feed_poll_minutes": 5}
        config["security"] = {
            "enable_preflight": True,
            "enable_output_safety": True,
        }

        return config

    def test_llm_connection(self, llm_config: dict[str, Any]) -> dict[str, Any]:
        """
        Test LLM connection with given settings.

        Returns result dict with success status and message.
        """
        try:
            import asyncio
            import aiohttp

            model = llm_config.get("model", "qwen3:8b")
            use_gateway = llm_config.get("use_gateway", False)

            if use_gateway:
                url = llm_config.get("gateway_url", "http://127.0.0.1:8200")
                health_url = f"{url}/health"
            else:
                url = "http://127.0.0.1:11434"
                health_url = f"{url}/v1/models"

            async def _check():
                async with aiohttp.ClientSession() as session:
                    async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        return resp.status == 200

            loop = asyncio.new_event_loop()
            try:
                ok = loop.run_until_complete(_check())
            finally:
                loop.close()

            if ok:
                return {"success": True, "message": f"Connected to LLM backend ({model})"}
            else:
                return {"success": False, "message": "LLM backend returned non-200 status"}

        except Exception as e:
            return {"success": False, "message": f"Connection failed: {e}"}
