"""
Provisioner — creates config files, encrypts secrets, and
sets up directory structure when the user clicks "Create Everything".

Idempotent: running twice does not break existing files (it merges/updates).
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def provision(base_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """
    Provision the Överblick installation from wizard state.

    Creates:
    - config/overblick.yaml (global LLM settings)
    - config/secrets/<identity>.yaml (encrypted secrets per agent)
    - data/<identity>/ directories
    - logs/<identity>/ directories

    Args:
        base_dir: Project root directory.
        state: Wizard state dict with principal, llm, communication,
               selected_characters, and agent_configs.

    Returns:
        Dict with 'created_files' list of paths created/updated.
    """
    created_files: list[str] = []

    # --- 1. Global config ---
    config_dir = base_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    global_config = _build_global_config(state)
    config_path = config_dir / "overblick.yaml"
    _write_yaml(config_path, global_config)
    created_files.append(str(config_path.relative_to(base_dir)))

    # --- 2. Secrets ---
    secrets_dir = config_dir / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)

    from overblick.core.security.secrets_manager import SecretsManager
    sm = SecretsManager(secrets_dir)

    principal = state.get("principal", {})
    comm = state.get("communication", {})
    selected = state.get("selected_characters", [])

    for char_name in selected:
        # Principal name — only write if provided (preserve existing when _has_* flag set)
        if principal.get("principal_name"):
            sm.set(char_name, "principal_name", principal["principal_name"])
        elif not state.get("_has_principal_name"):
            pass  # No new value and no existing — skip

        if principal.get("principal_email"):
            sm.set(char_name, "principal_email", principal["principal_email"])

        # Gmail secrets — respect _has_* flags for unchanged secrets
        if comm.get("gmail_enabled") and comm.get("gmail_address"):
            sm.set(char_name, "gmail_address", comm["gmail_address"])
            if comm.get("gmail_app_password"):
                sm.set(char_name, "gmail_app_password", comm["gmail_app_password"])
            # If no new password but existing one exists, keep it
            elif not state.get("_has_gmail_app_password"):
                pass

        # Telegram secrets
        if comm.get("telegram_enabled") and comm.get("telegram_bot_token"):
            sm.set(char_name, "telegram_bot_token", comm["telegram_bot_token"])
            if comm.get("telegram_chat_id"):
                sm.set(char_name, "telegram_chat_id", comm["telegram_chat_id"])
        elif comm.get("telegram_enabled") and not comm.get("telegram_bot_token"):
            # If enabled but no new token and existing one exists, keep it
            if not state.get("_has_telegram_bot_token"):
                pass

        created_files.append(f"config/secrets/{char_name}.yaml")

    # --- 3. Per-agent directories ---
    for char_name in selected:
        data_dir = base_dir / "data" / char_name
        data_dir.mkdir(parents=True, exist_ok=True)
        created_files.append(f"data/{char_name}/")

        log_dir = base_dir / "logs" / char_name
        log_dir.mkdir(parents=True, exist_ok=True)
        created_files.append(f"logs/{char_name}/")

    # --- 4. Per-agent config overrides (if non-default) ---
    agent_configs = state.get("agent_configs", {})
    for char_name, cfg in agent_configs.items():
        if char_name not in selected:
            continue
        override_path = config_dir / char_name / "config.yaml"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_data = _build_agent_override(cfg, state)
        if override_data:
            _write_yaml(override_path, override_data)
            created_files.append(str(override_path.relative_to(base_dir)))

    logger.info(
        "Provisioning complete: %d characters, %d files created",
        len(selected), len(created_files),
    )
    return {"created_files": created_files}


def _build_global_config(state: dict[str, Any]) -> dict[str, Any]:
    """Build the global overblick.yaml config from wizard state."""
    llm = state.get("llm", {})
    principal = state.get("principal", {})

    config: dict[str, Any] = {
        "framework": {
            "name": "Överblick",
            "version": "0.1.0",
        },
        "principal": {
            "timezone": principal.get("timezone", "Europe/Stockholm"),
            "language": principal.get("language_preference", "en"),
        },
        "llm": _build_llm_config(llm),
    }

    return config


def _build_llm_config(llm: dict[str, Any]) -> dict[str, Any]:
    """Build LLM config section in new backends format."""
    # Check if this is already new-format data (has 'local' or 'gateway_url' at top level)
    if "local" in llm or ("gateway_url" in llm and "llm_provider" not in llm):
        return _build_llm_config_new_format(llm)

    # Legacy format: flat provider-based
    return _build_llm_config_legacy(llm)


def _build_llm_config_new_format(llm: dict[str, Any]) -> dict[str, Any]:
    """Build LLM config from new backends-format wizard state."""
    local = llm.get("local", {})
    cloud = llm.get("cloud", {})
    openai = llm.get("openai", {})

    config: dict[str, Any] = {
        "gateway_url": llm.get("gateway_url", "http://127.0.0.1:8200"),
        "default_backend": llm.get("default_backend", "local"),
        "temperature": llm.get("default_temperature", 0.7),
        "max_tokens": llm.get("default_max_tokens", 2000),
        "backends": {
            "local": {
                "enabled": local.get("enabled", True),
                "type": local.get("backend_type", "ollama"),
                "host": local.get("host", "127.0.0.1"),
                "port": local.get("port", 11434),
                "model": local.get("model", "qwen3:8b"),
            },
            "cloud": {
                "enabled": cloud.get("enabled", False),
                "type": cloud.get("backend_type", "ollama"),
                "host": cloud.get("host", ""),
                "port": cloud.get("port", 11434),
                "model": cloud.get("model", "qwen3:8b"),
            },
            "openai": {
                "enabled": openai.get("enabled", False),
                "api_url": openai.get("api_url", "https://api.openai.com/v1"),
                "model": openai.get("model", "gpt-4o"),
            },
        },
    }
    return config


def _build_llm_config_legacy(llm: dict[str, Any]) -> dict[str, Any]:
    """Build LLM config from legacy flat-format wizard state (backward compat)."""
    provider = llm.get("llm_provider", "ollama")

    config: dict[str, Any] = {
        "gateway_url": llm.get("gateway_url", "http://127.0.0.1:8200"),
        "default_backend": "local",
        "temperature": llm.get("default_temperature", 0.7),
        "max_tokens": llm.get("default_max_tokens", 2000),
        "backends": {
            "local": {
                "enabled": True,
                "type": provider if provider in ("ollama", "lmstudio") else "ollama",
                "host": llm.get("ollama_host", "127.0.0.1"),
                "port": llm.get("ollama_port", 11434),
                "model": llm.get("model", "qwen3:8b"),
            },
            "cloud": {
                "enabled": False,
                "type": "ollama",
                "host": "",
                "port": 11434,
                "model": "qwen3:8b",
            },
            "openai": {
                "enabled": False,
                "api_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
            },
        },
    }

    # If provider was gateway, keep default_backend as local (gateway routes to it)
    # If provider was cloud/openai, set openai as the enabled backend
    if provider in ("cloud", "openai"):
        config["default_backend"] = "openai"
        config["backends"]["openai"] = {
            "enabled": True,
            "api_url": llm.get("cloud_api_url", "https://api.openai.com/v1"),
            "model": llm.get("cloud_model", "gpt-4o"),
        }

    return config


def _build_agent_override(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Build per-agent config override from wizard state."""
    llm_state = state.get("llm", {})
    override: dict[str, Any] = {}

    # Only store non-default values
    temp = cfg.get("temperature", 0.7)
    max_tokens = cfg.get("max_tokens", 2000)
    default_temp = llm_state.get("default_temperature", 0.7)
    default_max = llm_state.get("default_max_tokens", 2000)

    if temp != default_temp or max_tokens != default_max:
        override["llm"] = {}
        if temp != default_temp:
            override["llm"]["temperature"] = temp
        if max_tokens != default_max:
            override["llm"]["max_tokens"] = max_tokens

    heartbeat = cfg.get("heartbeat_hours", 4)
    if heartbeat != 4:
        override["schedule"] = {"heartbeat_hours": heartbeat}

    quiet = cfg.get("quiet_hours", True)
    if not quiet:
        override["quiet_hours"] = {"enabled": False}

    return override


def _write_yaml(path: Path, data: dict) -> None:
    """Write YAML file with clean formatting."""
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
