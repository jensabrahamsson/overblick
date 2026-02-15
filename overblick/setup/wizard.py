"""
Wizard state machine and route handlers for the setup flow.

Manages an 8-step wizard where state persists in app.state (single-user,
ephemeral server — no need for sessions or databases).

Steps:
1. Welcome
2. Principal Identity
3. LLM Configuration
4. Communication Channels
5. Character Select
6. Agent Configuration
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

from .validators import (
    AgentConfig,
    CharacterSelection,
    CommunicationData,
    LLMData,
    PrincipalData,
)

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent

# Default wizard state
_DEFAULT_STATE: dict[str, Any] = {
    "current_step": 1,
    "principal": {},
    "llm": {},
    "communication": {},
    "selected_characters": [],
    "agent_configs": {},
    "completed": False,
    "created_files": [],
}

# Plugin compatibility mapping per personality
PERSONALITY_PLUGINS: dict[str, dict[str, str]] = {
    "anomal": {
        "moltbook": "great",
        "telegram": "good",
        "ai_digest": "great",
    },
    "cherry": {
        "moltbook": "great",
        "consulting": "good",
    },
    "blixt": {
        "moltbook": "great",
        "discord": "good",
    },
    "bjork": {
        "moltbook": "great",
    },
    "prisma": {
        "moltbook": "great",
    },
    "rost": {
        "moltbook": "great",
    },
    "natt": {
        "moltbook": "great",
    },
    "stal": {
        "email_agent": "great",
        "gmail": "great",
        "telegram_notifier": "great",
    },
}


def _create_templates() -> Environment:
    """Create Jinja2 environment for setup templates."""
    env = Environment(
        loader=FileSystemLoader(str(_PKG_DIR / "templates")),
        autoescape=True,
    )
    return env


def _get_state(app: FastAPI) -> dict[str, Any]:
    """Get wizard state from app.state, initializing if needed."""
    if not hasattr(app.state, "wizard_state"):
        app.state.wizard_state = dict(_DEFAULT_STATE)
    return app.state.wizard_state


def _load_personality_data(base_dir: Path) -> list[dict[str, Any]]:
    """Load personality data from YAML files for the character select screen."""
    personalities_dir = base_dir / "overblick" / "personalities"
    characters = []

    if not personalities_dir.exists():
        return characters

    for pdir in sorted(personalities_dir.iterdir()):
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
                # Take first ~120 chars
                sample_quote = response.strip()[:120]
                if len(response.strip()) > 120:
                    sample_quote += "..."

        # Top 3 traits by value
        sorted_traits = sorted(traits.items(), key=lambda x: x[1], reverse=True)
        top_traits = sorted_traits[:3] if sorted_traits else []

        plugins = PERSONALITY_PLUGINS.get(pdir.name, {})

        characters.append({
            "name": pdir.name,
            "display_name": identity.get("display_name", pdir.name.capitalize()),
            "role": identity.get("role", identity.get("description", "")),
            "description": identity.get("description", ""),
            "base_tone": voice.get("base_tone", ""),
            "traits": {k: v for k, v in top_traits},
            "all_traits": traits,
            "sample_quote": sample_quote,
            "plugins": plugins,
            "operational": data.get("operational", {}),
        })

    return characters


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
                error=str(e),
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
            )
            state["llm"] = data.model_dump()
            return RedirectResponse("/step/4", status_code=303)
        except Exception as e:
            return _render("step3_llm.html", request, error=str(e))

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
            return _render("step4_communication.html", request, error=str(e))

    # --- Step 5: Character Select ---

    @app.get("/step/5", response_class=HTMLResponse)
    async def step5_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 5
        characters = _load_personality_data(request.app.state.base_dir)
        return _render(
            "step5_characters.html", request,
            characters=characters,
        )

    @app.post("/step/5", response_class=HTMLResponse)
    async def step5_post(request: Request):
        form = await request.form()
        selected = form.getlist("selected_characters")
        state = _get_state(request.app)
        try:
            data = CharacterSelection(selected_characters=selected)
            state["selected_characters"] = data.selected_characters
            return RedirectResponse("/step/6", status_code=303)
        except Exception as e:
            characters = _load_personality_data(request.app.state.base_dir)
            return _render(
                "step5_characters.html", request,
                characters=characters,
                error=str(e),
            )

    # --- Step 6: Agent Configuration ---

    @app.get("/step/6", response_class=HTMLResponse)
    async def step6_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 6
        characters = _load_personality_data(request.app.state.base_dir)
        selected_chars = [c for c in characters if c["name"] in state.get("selected_characters", [])]
        return _render(
            "step6_agent_config.html", request,
            selected_characters=selected_chars,
        )

    @app.post("/step/6", response_class=HTMLResponse)
    async def step6_post(request: Request):
        form = await request.form()
        state = _get_state(request.app)

        # Parse per-agent configs from form data
        agent_configs = {}
        for char_name in state.get("selected_characters", []):
            prefix = f"{char_name}_"
            agent_configs[char_name] = {
                "temperature": float(form.get(f"{prefix}temperature", 0.7)),
                "max_tokens": int(form.get(f"{prefix}max_tokens", 2000)),
                "heartbeat_hours": int(form.get(f"{prefix}heartbeat_hours", 4)),
                "quiet_hours": form.get(f"{prefix}quiet_hours", "on") == "on",
                "plugins": form.getlist(f"{prefix}plugins"),
                "capabilities": form.getlist(f"{prefix}capabilities"),
            }
        state["agent_configs"] = agent_configs
        return RedirectResponse("/step/7", status_code=303)

    # --- Step 7: Review ---

    @app.get("/step/7", response_class=HTMLResponse)
    async def step7_get(request: Request):
        state = _get_state(request.app)
        state["current_step"] = 7
        characters = _load_personality_data(request.app.state.base_dir)
        selected_chars = [c for c in characters if c["name"] in state.get("selected_characters", [])]
        return _render(
            "step7_review.html", request,
            selected_characters=selected_chars,
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
            characters = _load_personality_data(base_dir)
            selected_chars = [c for c in characters if c["name"] in state.get("selected_characters", [])]
            return _render(
                "step7_review.html", request,
                selected_characters=selected_chars,
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

    # --- Shutdown endpoint ---

    @app.post("/shutdown")
    async def shutdown():
        """Gracefully shut down the setup server."""
        def _stop():
            os.kill(os.getpid(), signal.SIGINT)
        threading.Timer(0.5, _stop).start()
        return {"status": "shutting_down"}
