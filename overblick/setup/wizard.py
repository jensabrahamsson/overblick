"""
Wizard state machine and route handlers for the setup flow.

Manages an 8-step wizard where state persists in app.state (single-user,
ephemeral server — no need for sessions or databases).

Steps:
1. Welcome
2. Principal Identity
3. LLM Configuration
4. Communication Channels
5. Use Cases — What should your agents do?
6. Assign Agents — Who handles what?
7. Review
8. Complete
"""

import logging
import os
import signal
import threading
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

from pydantic import ValidationError

from .validators import (
    AgentConfig,
    UseCaseSelection,
    CommunicationData,
    LLMData,
    PrincipalData,
)

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent

# Canonical display names for plugins (avoids "Email Agent agent" and "Ai Digest agent")
PLUGIN_DISPLAY_NAMES: dict[str, str] = {
    "moltbook": "Moltbook",
    "email_agent": "Email Agent",
    "telegram": "Telegram",
    "ai_digest": "AI Digest",
    "host_health": "Host Health",
    "kontrast": "Kontrast",
    "spegel": "Spegel",
    "skuggspel": "Skuggspel",
    "compass": "Compass",
    "stage": "Stage",
    "irc": "IRC",
}


def plugin_name(p: str) -> str:
    """Get display name for a plugin, with fallback to title-cased."""
    return PLUGIN_DISPLAY_NAMES.get(p, p.replace("_", " ").title())

# Default wizard state
_DEFAULT_STATE: dict[str, Any] = {
    "current_step": 1,
    "principal": {},
    "llm": {},
    "communication": {},
    "selected_use_cases": [],
    "assignments": {},  # use_case_id -> {personality, temperature, ...}
    "selected_characters": [],  # derived from assignments (for provisioner)
    "agent_configs": {},  # derived from assignments (for provisioner)
    "completed": False,
    "created_files": [],
}

# Use cases — what agents can do for the user
USE_CASES: list[dict[str, Any]] = [
    {
        "id": "social_media",
        "name": "Social Media",
        "description": "Post to forums and social platforms with personality-driven content",
        "icon": "\U0001F4AC",
        "plugins": ["moltbook"],
        "compatible_personalities": [
            "anomal", "cherry", "blixt", "bjork", "prisma", "rost", "natt",
        ],
        "recommended": "cherry",
    },
    {
        "id": "email",
        "name": "Email Management",
        "description": "Read, triage, draft replies, and send emails on your behalf",
        "icon": "\u2709\uFE0F",
        "plugins": ["email_agent"],
        "compatible_personalities": ["stal"],
        "recommended": "stal",
    },
    {
        "id": "notifications",
        "name": "Notifications",
        "description": "Send alerts and updates via Telegram based on agent activity",
        "icon": "\U0001F514",
        "plugins": ["telegram"],
        "compatible_personalities": ["anomal", "stal"],
        "recommended": "stal",
    },
    {
        "id": "research",
        "name": "News & Research",
        "description": "AI-curated digests of news, feeds, and topics you care about",
        "icon": "\U0001F50D",
        "plugins": ["ai_digest"],
        "compatible_personalities": ["anomal"],
        "recommended": "anomal",
    },
]

# Lookup: use_case_id -> use_case dict
_USE_CASE_MAP: dict[str, dict[str, Any]] = {uc["id"]: uc for uc in USE_CASES}


def _friendly_error(exc: Exception) -> str:
    """Extract a user-friendly error message from a Pydantic ValidationError."""
    if isinstance(exc, ValidationError):
        messages = []
        for err in exc.errors():
            msg = err.get("msg", "Invalid input")
            # Strip Pydantic prefix "Value error, "
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            messages.append(msg)
        return "; ".join(messages) if messages else "Please check your input."
    return str(exc)


def _create_templates() -> Environment:
    """Create Jinja2 environment for setup templates."""
    env = Environment(
        loader=FileSystemLoader(str(_PKG_DIR / "templates")),
        autoescape=True,
    )
    env.globals["plugin_name"] = plugin_name
    return env


def _get_state(app: FastAPI) -> dict[str, Any]:
    """Get wizard state from app.state, initializing if needed."""
    if not hasattr(app.state, "wizard_state"):
        app.state.wizard_state = dict(_DEFAULT_STATE)
    return app.state.wizard_state


def _load_identity_data(base_dir: Path) -> list[dict[str, Any]]:
    """Load identity data from YAML files for the character select screen."""
    identities_dir = base_dir / "overblick" / "identities"
    characters = []

    if not identities_dir.exists():
        return characters

    for pdir in sorted(identities_dir.iterdir()):
        yaml_path = pdir / "personality.yaml"
        if not pdir.is_dir() or not yaml_path.exists():
            continue

        # Skip the supervisor personality
        if pdir.name == "supervisor":
            continue

        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load personality %s: %s", pdir.name, e)
            continue

        identity = data.get("identity", {})
        voice = data.get("voice", {})
        traits = data.get("traits", {})
        examples = data.get("example_conversations", {})

        # Pick a sample quote from examples
        sample_quote = ""
        if examples:
            first_example = next(iter(examples.values()), {})
            response = first_example.get("response", first_example.get(
                "anomal_response", first_example.get(
                    "cherry_response", first_example.get(
                        "stal_response", ""))))
            if response:
                sample_quote = response.strip()[:120]
                if len(response.strip()) > 120:
                    sample_quote += "..."

        # Top 3 traits by value
        sorted_traits = sorted(traits.items(), key=lambda x: x[1], reverse=True)
        top_traits = sorted_traits[:3] if sorted_traits else []

        characters.append({
            "name": pdir.name,
            "display_name": identity.get("display_name", pdir.name.capitalize()),
            "role": identity.get("role", identity.get("description", "")),
            "description": identity.get("description", ""),
            "base_tone": voice.get("base_tone", ""),
            "traits": {k: v for k, v in top_traits},
            "all_traits": traits,
            "sample_quote": sample_quote,
            "operational": data.get("operational", {}),
        })

    return characters


def _build_assignment_data(
    selected_use_cases: list[str],
    all_characters: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build use-case assignment data for step 6 template."""
    char_by_name = {c["name"]: c for c in all_characters}
    assignments = state.get("assignments", {})
    result = []

    for uc_id in selected_use_cases:
        uc = _USE_CASE_MAP.get(uc_id)
        if not uc:
            continue

        compatible = []
        for p_name in uc["compatible_personalities"]:
            if p_name in char_by_name:
                compatible.append(char_by_name[p_name])

        # Get previously saved assignment for this use case
        prev = assignments.get(uc_id, {})

        result.append({
            "id": uc["id"],
            "name": uc["name"],
            "description": uc["description"],
            "icon": uc["icon"],
            "plugins": uc["plugins"],
            "recommended": uc["recommended"],
            "compatible": compatible,
            "assigned_personality": prev.get("personality", uc["recommended"]),
            "temperature": prev.get("temperature"),
            "max_tokens": prev.get("max_tokens"),
            "heartbeat_hours": prev.get("heartbeat_hours"),
            "quiet_hours": prev.get("quiet_hours", True),
        })

    return result


def _derive_provisioner_state(state: dict[str, Any]) -> None:
    """Derive selected_characters and agent_configs from assignments.

    The provisioner expects personality-centric data (selected_characters + agent_configs).
    This function converts from use-case-centric assignments to that format, merging
    plugins when multiple use cases share the same personality.
    """
    assignments = state.get("assignments", {})

    # Group by personality
    personality_plugins: dict[str, list[str]] = {}
    personality_config: dict[str, dict[str, Any]] = {}

    for uc_id, assignment in assignments.items():
        uc = _USE_CASE_MAP.get(uc_id)
        if not uc:
            continue

        personality = assignment.get("personality", "")
        if not personality:
            continue

        # Collect plugins from the use case
        if personality not in personality_plugins:
            personality_plugins[personality] = []
        personality_plugins[personality].extend(uc["plugins"])

        # First assignment wins for config (subsequent use cases share settings)
        if personality not in personality_config:
            personality_config[personality] = {
                "temperature": assignment.get("temperature", 0.7),
                "max_tokens": assignment.get("max_tokens", 2000),
                "heartbeat_hours": assignment.get("heartbeat_hours", 4),
                "quiet_hours": assignment.get("quiet_hours", True),
            }

    # Deduplicate and build provisioner-compatible format
    selected_characters = list(personality_plugins.keys())
    agent_configs = {}
    for p_name in selected_characters:
        cfg = dict(personality_config.get(p_name, {}))
        cfg["plugins"] = list(set(personality_plugins.get(p_name, [])))
        cfg["capabilities"] = []
        agent_configs[p_name] = cfg

    state["selected_characters"] = selected_characters
    state["agent_configs"] = agent_configs


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


def register_routes(app: FastAPI) -> None:
    """Register all wizard routes on the app."""
    env = _create_templates()

    def _render(template_name: str, request: Request, **kwargs) -> HTMLResponse:
        """Render a template with common context."""
        state = _get_state(request.app)
        tmpl = env.get_template(template_name)
        html = tmpl.render(
            request=request,
            state=state,
            version=_get_version(request.app.state.base_dir),
            **kwargs,
        )
        return HTMLResponse(html)

    # --- Step 1: Welcome ---

    @app.get("/", response_class=HTMLResponse)
    async def step1_welcome(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 1
        return _render("step1_welcome.html", request)

    # --- Step 2: Principal Identity ---

    @app.get("/step/2", response_class=HTMLResponse)
    async def step2_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 2
        return _render("step2_principal.html", request)

    @app.post("/step/2", response_class=HTMLResponse)
    async def step2_post(
        request: Request,
        principal_name: str = Form(""),
        principal_email: str = Form(""),
        timezone: str = Form("Europe/Stockholm"),
        language_preference: str = Form("en"),
    ):
        state = _get_state(request.app)
        try:
            data = PrincipalData(
                principal_name=principal_name,
                principal_email=principal_email,
                timezone=timezone,
                language_preference=language_preference,
            )
            state["principal"] = data.model_dump()
            return RedirectResponse("/step/3", status_code=303)
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

    # --- Step 3: LLM Configuration ---

    @app.get("/step/3", response_class=HTMLResponse)
    async def step3_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 3
        return _render("step3_llm.html", request)

    @app.post("/step/3", response_class=HTMLResponse)
    async def step3_post(
        request: Request,
        llm_provider: str = Form("ollama"),
        ollama_host: str = Form("127.0.0.1"),
        ollama_port: int = Form(11434),
        model: str = Form("qwen3:8b"),
        gateway_url: str = Form("http://127.0.0.1:8200"),
        default_temperature: float = Form(0.7),
        default_max_tokens: int = Form(2000),
        cloud_api_url: str = Form(""),
        cloud_model: str = Form(""),
    ):
        state = _get_state(request.app)
        try:
            data = LLMData(
                llm_provider=llm_provider,
                ollama_host=ollama_host,
                ollama_port=ollama_port,
                model=model,
                gateway_url=gateway_url,
                default_temperature=default_temperature,
                default_max_tokens=default_max_tokens,
                cloud_api_url=cloud_api_url,
                cloud_model=cloud_model,
            )
            state["llm"] = data.model_dump()
            return RedirectResponse("/step/4", status_code=303)
        except Exception as e:
            return _render("step3_llm.html", request, error=_friendly_error(e))

    # --- Step 4: Communication Channels ---

    @app.get("/step/4", response_class=HTMLResponse)
    async def step4_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 4
        return _render("step4_communication.html", request)

    @app.post("/step/4", response_class=HTMLResponse)
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
            return RedirectResponse("/step/5", status_code=303)
        except Exception as e:
            return _render("step4_communication.html", request, error=_friendly_error(e))

    # --- Step 5: Use Cases ---

    @app.get("/step/5", response_class=HTMLResponse)
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

    @app.post("/step/5", response_class=HTMLResponse)
    async def step5_post(request: Request):
        form = await request.form()
        selected = form.getlist("selected_use_cases")
        state = _get_state(request.app)
        try:
            data = UseCaseSelection(selected_use_cases=selected)
            state["selected_use_cases"] = data.selected_use_cases
            return RedirectResponse("/step/6", status_code=303)
        except Exception as e:
            comm = state.get("communication", {})
            return _render(
                "step5_usecases.html", request,
                use_cases=USE_CASES,
                gmail_enabled=comm.get("gmail_enabled", False),
                telegram_enabled=comm.get("telegram_enabled", False),
                error=_friendly_error(e),
            )

    # --- Step 6: Assign Agents ---

    @app.get("/step/6", response_class=HTMLResponse)
    async def step6_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 6
        characters = _load_identity_data(request.app.state.base_dir)
        assignment_data = _build_assignment_data(
            state.get("selected_use_cases", []),
            characters,
            state,
        )
        return _render(
            "step6_agent_config.html", request,
            assignment_data=assignment_data,
        )

    @app.post("/step/6", response_class=HTMLResponse)
    async def step6_post(request: Request):
        form = await request.form()
        state = _get_state(request.app)

        # Parse per-use-case assignments from form data
        assignments = {}
        for uc_id in state.get("selected_use_cases", []):
            uc = _USE_CASE_MAP.get(uc_id)
            if not uc:
                continue

            personality = form.get(f"{uc_id}_personality", "")

            # Server-side allowlist: personality must be compatible with the use case
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
        return RedirectResponse("/step/7", status_code=303)

    # --- Step 7: Review ---

    @app.get("/step/7", response_class=HTMLResponse)
    async def step7_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 7
        characters = _load_identity_data(request.app.state.base_dir)
        char_by_name = {c["name"]: c for c in characters}

        # Build review-friendly assignment list
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
        )

    @app.post("/step/7", response_class=HTMLResponse)
    async def step7_post(request: Request):
        """Execute provisioning and redirect to complete."""
        state = _get_state(request.app)
        base_dir = request.app.state.base_dir

        from .provisioner import provision
        try:
            result = provision(base_dir, state)
            state["created_files"] = result.get("created_files", [])
            state["completed"] = True
            return RedirectResponse("/step/8", status_code=303)
        except Exception as e:
            logger.error("Provisioning failed: %s", e, exc_info=True)
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

    @app.get("/step/8", response_class=HTMLResponse)
    async def step8_complete(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 8
        return _render("step8_complete.html", request)

    # --- Test endpoints (htmx) ---

    @app.post("/test/ollama", response_class=HTMLResponse)
    async def test_ollama(request: Request):
        """Test Ollama connection and return available models."""
        form = await request.form()
        host = form.get("ollama_host", "127.0.0.1")
        port = form.get("ollama_port", "11434")
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

    @app.post("/test/gmail", response_class=HTMLResponse)
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
            return HTMLResponse(f'<span class="badge badge-red">Failed</span><span class="test-detail">{msg}</span>')

    @app.post("/test/telegram", response_class=HTMLResponse)
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
                msg = f'Connected as @{bot_name}'
                if chat_id:
                    # Try sending a test message
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
            return HTMLResponse(f'<span class="badge badge-red">Failed</span><span class="test-detail">{e}</span>')

    # --- Chat endpoint ---

    @app.post("/chat")
    async def chat(request: Request):
        """Chat with an identity during setup (LLM-powered)."""
        from fastapi.responses import JSONResponse

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"success": False, "error": "Invalid JSON body."},
                status_code=400,
            )

        identity_name = body.get("identity_name", "")
        message = body.get("message", "")

        if not identity_name or not message:
            return JSONResponse(
                {"success": False, "error": "identity_name and message are required."},
                status_code=400,
            )

        llm_config = state.get("llm", {})

        from overblick.shared.onboarding_chat import chat_with_identity
        result = await chat_with_identity(identity_name, message, llm_config)

        return JSONResponse(result)

    @app.post("/test-llm")
    async def test_llm(request: Request):
        """Test LLM connection during setup."""
        from fastapi.responses import JSONResponse

        llm_config = state.get("llm", {})
        if not llm_config:
            return JSONResponse(
                {"success": False, "error": "Configure LLM settings first."},
                status_code=400,
            )

        from overblick.shared.onboarding_chat import test_llm_connection
        result = await test_llm_connection(llm_config)

        return JSONResponse(result)

    # --- Shutdown endpoint ---

    @app.post("/shutdown")
    async def shutdown():
        """Gracefully shut down the setup server."""
        def _stop():
            os.kill(os.getpid(), signal.SIGINT)
        threading.Timer(0.5, _stop).start()
        return {"status": "shutting_down"}
