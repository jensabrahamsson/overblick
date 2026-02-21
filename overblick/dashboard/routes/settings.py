"""
Settings wizard routes — integrated 8-step configuration wizard.

Replaces the standalone python -m overblick.setup process. Reuses all
validator and provisioner logic from overblick.setup unchanged. Wizard
state is stored on app.state between requests (ephemeral, single-user).

Gateway-as-router architecture: Gateway is always-on infrastructure.
Backends (local, cloud, openai) are the actual inference targets.

Steps:
1. Welcome / overview
2. Principal (name, email, timezone)
3. LLM backends (local, cloud, openai — all through gateway)
4. Communication (Gmail, Telegram)
5. Use cases
6. Identity assignment
7. Review
8. Complete
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from overblick.setup.validators import (
    AgentConfig,
    BackendConfig,
    CommunicationData,
    LLMData,
    OpenAIConfig,
    PrincipalData,
    UseCaseSelection,
)
from overblick.setup.wizard import (
    PLUGIN_DISPLAY_NAMES,
    USE_CASES,
    _USE_CASE_MAP,
    _build_assignment_data,
    _derive_provisioner_state,
    _friendly_error,
    _get_state,
    _load_identity_data,
    plugin_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings")


def _get_base_dir(request: Request) -> Path:
    """Get project base directory from app config."""
    cfg = request.app.state.config
    if cfg.base_dir:
        return Path(cfg.base_dir)
    return Path(__file__).parent.parent.parent.parent


def _load_existing_config(base_dir: Path) -> dict[str, Any]:
    """Load existing overblick.yaml if it exists, returning empty dict otherwise."""
    config_file = base_dir / "config" / "overblick.yaml"
    if not config_file.exists():
        return {}
    try:
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load existing config: %s", e)
        return {}


def _check_secret_exists(base_dir: Path, identity: str, key: str) -> bool:
    """Check if a secret key exists for an identity (without reading its value)."""
    secrets_file = base_dir / "config" / "secrets" / f"{identity}.yaml"
    if not secrets_file.exists():
        return False
    try:
        with open(secrets_file) as f:
            data = yaml.safe_load(f) or {}
        return key in data
    except Exception:
        return False


def _config_to_wizard_state(cfg: dict[str, Any], base_dir: Path | None = None) -> dict[str, Any]:
    """Map overblick.yaml keys to wizard state dict format for pre-population."""
    state_update: dict[str, Any] = {}

    principal = cfg.get("principal", {})
    if principal:
        state_update["principal"] = {
            "principal_name": "",  # secrets — can't pre-fill
            "principal_email": "",
            "timezone": principal.get("timezone", "Europe/Stockholm"),
            "language_preference": principal.get("language", "en"),
        }

    llm = cfg.get("llm", {})
    if llm:
        # New format: has 'backends' section
        if "backends" in llm:
            state_update["llm"] = _parse_new_llm_config(llm)
        else:
            # Old format: flat provider-based
            state_update["llm"] = _migrate_old_llm_config(llm)

    # Check which secrets exist (set _has_* flags)
    if base_dir:
        # Check secrets for any identity that has a secrets file
        secrets_dir = base_dir / "config" / "secrets"
        if secrets_dir.exists():
            for sf in secrets_dir.iterdir():
                if sf.suffix == ".yaml" and sf.stem != "__pycache__":
                    identity = sf.stem
                    for key in ("principal_name", "principal_email", "gmail_app_password",
                                "telegram_bot_token", "telegram_chat_id"):
                        if _check_secret_exists(base_dir, identity, key):
                            state_update[f"_has_{key}"] = True
                    break  # Only need to check one identity

    return state_update


def _parse_new_llm_config(llm: dict[str, Any]) -> dict[str, Any]:
    """Parse new backends-format LLM config into wizard state."""
    backends = llm.get("backends", {})
    local = backends.get("local", {})
    cloud = backends.get("cloud", {})
    openai = backends.get("openai", {})

    return {
        "gateway_url": llm.get("gateway_url", "http://127.0.0.1:8200"),
        "local": {
            "enabled": local.get("enabled", True),
            "backend_type": local.get("type", "ollama"),
            "host": local.get("host", "127.0.0.1"),
            "port": local.get("port", 11434),
            "model": local.get("model", "qwen3:8b"),
        },
        "cloud": {
            "enabled": cloud.get("enabled", False),
            "backend_type": cloud.get("type", "ollama"),
            "host": cloud.get("host", ""),
            "port": cloud.get("port", 11434),
            "model": cloud.get("model", "qwen3:8b"),
        },
        "openai": {
            "enabled": openai.get("enabled", False),
            "api_url": openai.get("api_url", "https://api.openai.com/v1"),
            "model": openai.get("model", "gpt-4o"),
        },
        "default_backend": llm.get("default_backend", "local"),
        "default_temperature": llm.get("temperature", 0.7),
        "default_max_tokens": llm.get("max_tokens", 2000),
    }


def _migrate_old_llm_config(llm: dict[str, Any]) -> dict[str, Any]:
    """Migrate old flat-format LLM config to new backends wizard state."""
    provider = llm.get("provider", "ollama")
    host = llm.get("host", "127.0.0.1")
    port = llm.get("port", 11434)
    model = llm.get("model", "qwen3:8b")

    state = {
        "gateway_url": llm.get("gateway_url", "http://127.0.0.1:8200"),
        "local": {
            "enabled": provider in ("ollama", "lmstudio", "gateway"),
            "backend_type": provider if provider in ("ollama", "lmstudio") else "ollama",
            "host": host,
            "port": port,
            "model": model,
        },
        "cloud": {
            "enabled": False,
            "backend_type": "ollama",
            "host": "",
            "port": 11434,
            "model": "qwen3:8b",
        },
        "openai": {
            "enabled": provider in ("cloud", "openai"),
            "api_url": llm.get("cloud_api_url", "https://api.openai.com/v1"),
            "model": llm.get("cloud_model", "gpt-4o"),
        },
        "default_backend": "openai" if provider in ("cloud", "openai") else "local",
        "default_temperature": llm.get("temperature", 0.7),
        "default_max_tokens": llm.get("max_tokens", 2000),
    }
    return state


def _get_version(base_dir: Path) -> str:
    """Get version from pyproject.toml."""
    try:
        toml_path = base_dir / "pyproject.toml"
        if toml_path.exists():
            content = toml_path.read_text()
            for line in content.split("\n"):
                if line.strip().startswith("version"):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.1.0"


def _render(template_name: str, request: Request, **kwargs) -> HTMLResponse:
    """Render a settings template with common context."""
    templates = request.app.state.templates
    base_dir = _get_base_dir(request)
    state = _get_state(request.app)
    existing_cfg = _load_existing_config(base_dir)
    return templates.TemplateResponse(f"settings/{template_name}", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "state": state,
        "pre_populated": bool(existing_cfg),
        "version": _get_version(base_dir),
        **kwargs,
    })


# --- Step 1: Welcome ---

@router.get("/", response_class=HTMLResponse)
async def settings_root(request: Request):
    """Redirect to step 1."""
    return RedirectResponse("/settings/step/1", status_code=302)


@router.get("/step/1", response_class=HTMLResponse)
async def step1_get(request: Request):
    """Welcome / overview."""
    base_dir = _get_base_dir(request)
    state = _get_state(request.app)
    state["current_step"] = 1

    # Pre-populate from existing config on first visit
    existing_cfg = _load_existing_config(base_dir)
    if not state.get("_pre_populated") and existing_cfg:
        state.update(_config_to_wizard_state(existing_cfg, base_dir))
        state["_pre_populated"] = True

    return _render("step1_welcome.html", request)


@router.post("/step/1", response_class=HTMLResponse)
async def step1_post(request: Request):
    return RedirectResponse("/settings/step/2", status_code=303)


# --- Step 2: Principal ---

@router.get("/step/2", response_class=HTMLResponse)
async def step2_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 2
    return _render("step2_principal.html", request)


@router.post("/step/2", response_class=HTMLResponse)
async def step2_post(
    request: Request,
    principal_name: str = Form(""),
    principal_email: str = Form(""),
    timezone: str = Form("Europe/Stockholm"),
    language_preference: str = Form("en"),
):
    state = _get_state(request.app)

    # If reconfiguring and no new name provided, keep the has-flag
    if not principal_name and state.get("_has_principal_name"):
        # Allow empty name when reconfiguring (preserve existing secret)
        state["principal"] = {
            "principal_name": "",
            "principal_email": principal_email,
            "timezone": timezone,
            "language_preference": language_preference,
        }
        return RedirectResponse("/settings/step/3", status_code=303)

    try:
        data = PrincipalData(
            principal_name=principal_name,
            principal_email=principal_email,
            timezone=timezone,
            language_preference=language_preference,
        )
        state["principal"] = data.model_dump()
        return RedirectResponse("/settings/step/3", status_code=303)
    except Exception as e:
        return _render(
            "step2_principal.html", request,
            error=_friendly_error(e),
            form_data={
                "principal_name": principal_name,
                "principal_email": principal_email,
                "timezone": timezone,
                "language_preference": language_preference,
            },
        )


# --- Step 3: LLM Backends ---

@router.get("/step/3", response_class=HTMLResponse)
async def step3_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 3
    return _render("step3_llm.html", request)


@router.post("/step/3", response_class=HTMLResponse)
async def step3_post(request: Request):
    form = await request.form()
    state = _get_state(request.app)

    try:
        # Map UI field names (from step3_llm.html template) to LLMData format
        provider = form.get("llm_provider", "ollama")
        host = form.get("ollama_host", "127.0.0.1")
        port_str = form.get("ollama_port", "11434")
        model = form.get("model", "qwen3:8b")
        gateway_url = form.get("gateway_url", "http://127.0.0.1:8200")

        is_local = provider in ("ollama", "lmstudio")
        is_gateway = provider == "gateway"
        is_openai = provider == "openai"

        data = LLMData(
            gateway_url=gateway_url,
            local=BackendConfig(
                enabled=is_local or is_gateway,
                backend_type=provider if is_local else "ollama",
                host=host,
                port=int(port_str),
                model=model,
            ),
            cloud=BackendConfig(
                enabled=False,
                backend_type="ollama",
                host="",
                port=11434,
                model="qwen3:8b",
            ),
            openai=OpenAIConfig(
                enabled=is_openai,
                api_url=form.get("cloud_api_url", "https://api.openai.com/v1"),
                model=form.get("cloud_model", "gpt-4o"),
            ),
            default_backend="openai" if is_openai else "local",
            default_temperature=float(form.get("default_temperature", "0.7")),
            default_max_tokens=int(form.get("default_max_tokens", "2000")),
        )
        state["llm"] = data.model_dump()
        return RedirectResponse("/settings/step/4", status_code=303)
    except Exception as e:
        return _render("step3_llm.html", request, error=_friendly_error(e))


# --- Step 4: Communication Channels ---

@router.get("/step/4", response_class=HTMLResponse)
async def step4_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 4
    return _render("step4_communication.html", request)


@router.post("/step/4", response_class=HTMLResponse)
async def step4_post(
    request: Request,
    gmail_enabled: str = Form("off"),
    gmail_address: str = Form(""),
    gmail_app_password: str = Form(""),
    telegram_enabled: str = Form("off"),
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
):
    state = _get_state(request.app)
    try:
        data = CommunicationData(
            gmail_enabled=gmail_enabled == "on",
            gmail_address=gmail_address,
            gmail_app_password=gmail_app_password,
            telegram_enabled=telegram_enabled == "on",
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        state["communication"] = data.model_dump()
        return RedirectResponse("/settings/step/5", status_code=303)
    except Exception as e:
        return _render("step4_communication.html", request, error=_friendly_error(e))


# --- Step 5: Use Cases ---

@router.get("/step/5", response_class=HTMLResponse)
async def step5_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 5
    comm = state.get("communication", {})
    return _render(
        "step5_usecases.html", request,
        use_cases=USE_CASES,
        gmail_enabled=comm.get("gmail_enabled", False),
        telegram_enabled=comm.get("telegram_enabled", False),
    )


@router.post("/step/5", response_class=HTMLResponse)
async def step5_post(request: Request):
    form = await request.form()
    selected = form.getlist("selected_use_cases")
    state = _get_state(request.app)
    try:
        data = UseCaseSelection(selected_use_cases=selected)
        state["selected_use_cases"] = data.selected_use_cases
        return RedirectResponse("/settings/step/6", status_code=303)
    except Exception as e:
        comm = state.get("communication", {})
        return _render(
            "step5_usecases.html", request,
            use_cases=USE_CASES,
            gmail_enabled=comm.get("gmail_enabled", False),
            telegram_enabled=comm.get("telegram_enabled", False),
            error=_friendly_error(e),
        )


# --- Step 6: Identity Assignment ---

@router.get("/step/6", response_class=HTMLResponse)
async def step6_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 6
    base_dir = _get_base_dir(request)
    characters = _load_identity_data(base_dir)
    assignment_data = _build_assignment_data(
        state.get("selected_use_cases", []),
        characters,
        state,
    )
    return _render("step6_assignment.html", request, assignment_data=assignment_data)


@router.post("/step/6", response_class=HTMLResponse)
async def step6_post(request: Request):
    form = await request.form()
    state = _get_state(request.app)

    assignments = {}
    for uc_id in state.get("selected_use_cases", []):
        uc = _USE_CASE_MAP.get(uc_id)
        if not uc:
            continue
        personality = form.get(f"{uc_id}_personality", "")
        allowed = uc["compatible_personalities"]
        if personality not in allowed:
            personality = uc["recommended"]
        assignments[uc_id] = {
            "personality": personality,
            "temperature": float(form.get(f"{uc_id}_temperature", 0.7)),
            "max_tokens": int(form.get(f"{uc_id}_max_tokens", 2000)),
            "heartbeat_hours": int(form.get(f"{uc_id}_heartbeat_hours", 4)),
            "quiet_hours": form.get(f"{uc_id}_quiet_hours", "off") == "on",
        }

    state["assignments"] = assignments
    _derive_provisioner_state(state)
    return RedirectResponse("/settings/step/7", status_code=303)


# --- Step 7: Review ---

@router.get("/step/7", response_class=HTMLResponse)
async def step7_get(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 7
    base_dir = _get_base_dir(request)
    characters = _load_identity_data(base_dir)
    char_by_name = {c["name"]: c for c in characters}

    review_assignments = []
    for uc_id in state.get("selected_use_cases", []):
        uc = _USE_CASE_MAP.get(uc_id, {})
        assignment = state.get("assignments", {}).get(uc_id, {})
        personality_name = assignment.get("personality", "")
        char_data = char_by_name.get(personality_name, {})
        review_assignments.append({
            "use_case": uc.get("name", uc_id),
            "use_case_icon": uc.get("icon", ""),
            "plugins": uc.get("plugins", []),
            "personality_name": personality_name,
            "personality_display": char_data.get(
                "display_name", personality_name.capitalize()
            ),
            "personality_role": char_data.get("role", ""),
        })

    return _render("step7_review.html", request, review_assignments=review_assignments)


@router.post("/step/7", response_class=HTMLResponse)
async def step7_post(request: Request):
    """Execute provisioning and redirect to complete."""
    state = _get_state(request.app)
    base_dir = _get_base_dir(request)

    from overblick.setup.provisioner import provision
    try:
        result = provision(base_dir, state)
        state["created_files"] = result.get("created_files", [])
        state["completed"] = True
        # Clear first-run flag now that config exists
        request.app.state.setup_needed = False
        return RedirectResponse("/settings/step/8", status_code=303)
    except Exception as e:
        logger.error("Provisioning failed: %s", e, exc_info=True)
        base_dir = _get_base_dir(request)
        characters = _load_identity_data(base_dir)
        char_by_name = {c["name"]: c for c in characters}
        review_assignments = []
        for uc_id in state.get("selected_use_cases", []):
            uc = _USE_CASE_MAP.get(uc_id, {})
            assignment = state.get("assignments", {}).get(uc_id, {})
            personality_name = assignment.get("personality", "")
            char_data = char_by_name.get(personality_name, {})
            review_assignments.append({
                "use_case": uc.get("name", uc_id),
                "use_case_icon": uc.get("icon", ""),
                "plugins": uc.get("plugins", []),
                "personality_name": personality_name,
                "personality_display": char_data.get(
                    "display_name", personality_name.capitalize()
                ),
                "personality_role": char_data.get("role", ""),
            })
        return _render(
            "step7_review.html", request,
            review_assignments=review_assignments,
            error=f"Setup failed: {e}",
        )


# --- Step 8: Complete ---

@router.get("/step/8", response_class=HTMLResponse)
async def step8_complete(request: Request):
    state = _get_state(request.app)
    state["current_step"] = 8
    return _render("step8_complete.html", request)


# --- Test endpoints ---

@router.post("/test/ollama", response_class=HTMLResponse)
async def test_ollama(request: Request):
    """Test Ollama / LM Studio connection and return available models."""
    form = await request.form()
    host = form.get("host", form.get("ollama_host", "127.0.0.1"))
    port = form.get("port", form.get("ollama_port", "11434"))
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{host}:{port}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            if models:
                model_list = ", ".join(models[:10])
                return HTMLResponse(
                    f'<span class="badge badge-green">Connected</span>'
                    f'<span class="test-detail">Models: {model_list}</span>'
                )
            return HTMLResponse(
                '<span class="badge badge-green">Connected</span>'
                '<span class="test-detail">No models found. Pull one with: ollama pull qwen3:8b</span>'
            )
    except Exception as e:
        return HTMLResponse(
            f'<span class="badge badge-red">Not reachable</span>'
            f'<span class="test-detail">{e}</span>'
        )


@router.post("/api/models", response_class=HTMLResponse)
async def fetch_models(request: Request):
    """Fetch available models from an Ollama/LM Studio instance.

    Returns <select> HTML options for populating a model dropdown via HTMX.
    """
    form = await request.form()
    host = form.get("host", "127.0.0.1")
    port = form.get("port", "11434")
    current_model = form.get("current_model", "")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"http://{host}:{port}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
        if models:
            options = ""
            for m in models:
                selected = ' selected' if m == current_model else ''
                options += f'<option value="{m}"{selected}>{m}</option>'
            return HTMLResponse(f'<select class="form-select" name="{{{{field_name}}}}">{options}</select>')
        return HTMLResponse('<span class="badge badge-amber">No models found</span>')
    except Exception as e:
        return HTMLResponse(f'<span class="badge badge-red">Error: {e}</span>')


@router.post("/test/gateway", response_class=HTMLResponse)
async def test_gateway(request: Request):
    """Test Gateway connection."""
    form = await request.form()
    url = form.get("gateway_url", "http://127.0.0.1:8200")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "unknown")
            if status == "healthy":
                return HTMLResponse('<span class="badge badge-green">Connected</span>')
            return HTMLResponse(f'<span class="badge badge-amber">{status}</span>')
    except Exception as e:
        return HTMLResponse(
            f'<span class="badge badge-red">Not reachable</span>'
            f'<span class="test-detail">{e}</span>'
        )


@router.post("/test/gmail", response_class=HTMLResponse)
async def test_gmail(request: Request):
    """Test Gmail IMAP connection."""
    form = await request.form()
    address = form.get("gmail_address", "")
    password = form.get("gmail_app_password", "")
    if not address or not password:
        return HTMLResponse('<span class="badge badge-amber">Enter credentials first</span>')
    try:
        import imaplib
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(address, password)
        imap.logout()
        return HTMLResponse('<span class="badge badge-green">Connected</span>')
    except Exception as e:
        msg = str(e)
        if "AUTHENTICATIONFAILED" in msg:
            msg = "Authentication failed. Check your App Password."
        return HTMLResponse(
            f'<span class="badge badge-red">Failed</span>'
            f'<span class="test-detail">{msg}</span>'
        )


@router.post("/test/telegram", response_class=HTMLResponse)
async def test_telegram(request: Request):
    """Test Telegram bot connection."""
    form = await request.form()
    token = form.get("telegram_bot_token", "")
    chat_id = form.get("telegram_chat_id", "")
    if not token:
        return HTMLResponse('<span class="badge badge-amber">Enter bot token first</span>')
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            resp.raise_for_status()
            bot_data = resp.json()
            bot_name = bot_data.get("result", {}).get("username", "unknown")
            msg = f"Connected as @{bot_name}"
            if chat_id:
                send_resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "Överblick setup test message"},
                )
                if send_resp.is_success:
                    msg += " — test message sent!"
                else:
                    msg += " — bot OK but could not send to chat ID"
            return HTMLResponse(f'<span class="badge badge-green">{msg}</span>')
    except Exception as e:
        return HTMLResponse(
            f'<span class="badge badge-red">Failed</span>'
            f'<span class="test-detail">{e}</span>'
        )


@router.post("/test-llm", response_class=HTMLResponse)
async def test_llm(request: Request):
    """Test LLM connection."""
    from fastapi.responses import JSONResponse
    state = _get_state(request.app)
    llm_config = state.get("llm", {})
    if not llm_config:
        return JSONResponse(
            {"success": False, "error": "Configure LLM settings first."},
            status_code=400,
        )
    from overblick.shared.onboarding_chat import test_llm_connection
    result = await test_llm_connection(llm_config)
    return JSONResponse(result)
