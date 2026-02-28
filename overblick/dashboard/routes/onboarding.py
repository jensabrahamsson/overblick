"""
Onboarding wizard routes — 7-step identity creation.

Wizard state is stored server-side in memory, keyed by session CSRF token.
This ensures tamper-proof state without cookie size limitations.
Each step validates input via Pydantic before proceeding.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..security import OnboardingNameForm, OnboardingLLMForm

logger = logging.getLogger(__name__)

router = APIRouter()

# Wizard steps in order (LLM before personality so chat is available early)
STEPS = [
    "name", "llm", "personality", "plugins",
    "secrets", "review", "verify",
]

# Server-side wizard state storage, keyed by session CSRF token.
# This avoids the itsdangerous signed cookie immutability problem.
_wizard_states: dict[str, dict[str, Any]] = {}

# Limit to prevent memory exhaustion (max concurrent wizard sessions)
_MAX_WIZARD_SESSIONS = 50


def _get_session_key(request: Request) -> str:
    """Get session key for wizard state lookup."""
    return request.state.session.get("csrf_token", "")


def _get_wizard_state(request: Request) -> dict:
    """Get wizard state from server-side storage."""
    key = _get_session_key(request)
    return _wizard_states.get(key, {})


def _set_wizard_state(request: Request, state: dict) -> None:
    """Store wizard state server-side."""
    key = _get_session_key(request)
    if not key:
        return

    # Evict oldest sessions if at capacity
    if len(_wizard_states) >= _MAX_WIZARD_SESSIONS and key not in _wizard_states:
        oldest_key = next(iter(_wizard_states))
        del _wizard_states[oldest_key]

    _wizard_states[key] = state


def _step_url(step: int) -> str:
    """Get URL for a wizard step (1-indexed)."""
    return f"/onboard?step={step}"


@router.get("/onboard", response_class=HTMLResponse)
async def onboard_page(request: Request):
    """Render current onboarding wizard step."""
    templates = request.app.state.templates
    config = request.app.state.config

    try:
        step = int(request.query_params.get("step", "1"))
    except ValueError:
        step = 1
    step = max(1, min(step, len(STEPS)))

    step_name = STEPS[step - 1]
    wizard_state = _get_wizard_state(request)

    # Gather context data for the step
    context = {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "step": step,
        "total_steps": len(STEPS),
        "step_name": step_name,
        "steps": STEPS,
        "wizard": wizard_state,
    }

    # Step-specific data
    if step_name == "personality":
        personality_svc = request.app.state.personality_service
        context["available_personalities"] = personality_svc.list_identities()

    elif step_name == "plugins":
        system_svc = request.app.state.system_service
        context["available_plugins"] = system_svc.get_available_plugins()
        context["capability_bundles"] = system_svc.get_capability_bundles()
        # Personalities for chat selector
        personality_svc = request.app.state.personality_service
        context["available_personalities"] = personality_svc.list_identities()

    elif step_name == "review":
        context["summary"] = _build_summary(wizard_state, request)
        # Personalities for chat selector
        personality_svc = request.app.state.personality_service
        context["available_personalities"] = personality_svc.list_identities()

    elif step_name == "verify":
        context["result"] = wizard_state.get("creation_result", {})

    template_name = f"onboarding/step{step}_{step_name}.html"
    return templates.TemplateResponse(template_name, context)


@router.post("/onboard", response_class=HTMLResponse)
async def onboard_submit(request: Request):
    """Process wizard step submission."""
    templates = request.app.state.templates

    form = await request.form()
    try:
        step = int(form.get("step", "1"))
    except ValueError:
        step = 1
    step = max(1, min(step, len(STEPS)))
    step_name = STEPS[step - 1]

    # CSRF is enforced by AuthMiddleware via X-CSRF-Token header

    wizard_state = _get_wizard_state(request)
    error = None

    # Process step
    if step_name == "name":
        try:
            data = OnboardingNameForm(
                name=form.get("name", ""),
                description=form.get("description", ""),
                display_name=form.get("display_name", ""),
            )
            onboarding_svc = request.app.state.onboarding_service
            if onboarding_svc.identity_exists(data.name):
                error = f"Identity '{data.name}' already exists."
            else:
                wizard_state["name"] = data.name
                wizard_state["description"] = data.description
                wizard_state["display_name"] = data.display_name
        except Exception as e:
            error = str(e)

    elif step_name == "personality":
        personality = form.get("personality", "")
        if personality:
            personality_svc = request.app.state.personality_service
            available = personality_svc.list_identities()
            if personality not in available:
                error = f"Unknown personality: '{personality}'"
        if not error:
            wizard_state["personality"] = personality

    elif step_name == "llm":
        try:
            data = OnboardingLLMForm(
                model=form.get("model", "qwen3:8b"),
                temperature=float(form.get("temperature", "0.7")),
                max_tokens=int(form.get("max_tokens", "2000")),
                provider=form.get("provider", "ollama"),
                cloud_api_url=form.get("cloud_api_url", ""),
                cloud_model=form.get("cloud_model", ""),
            )
            wizard_state["llm"] = data.model_dump()
        except Exception as e:
            error = str(e)

    elif step_name == "plugins":
        system_svc = request.app.state.system_service
        available_plugins = set(system_svc.get_available_plugins())
        plugins = [p for p in form.getlist("plugins") if p in available_plugins]
        capabilities = form.getlist("capabilities")
        wizard_state["plugins"] = plugins
        wizard_state["capabilities"] = capabilities

    elif step_name == "secrets":
        keys = form.getlist("secret_keys")
        values = form.getlist("secret_values")
        secrets = {}
        for k, v in zip(keys, values):
            if k and v:
                secrets[k] = v
        wizard_state["secrets"] = secrets

    elif step_name == "review":
        onboarding_svc = request.app.state.onboarding_service
        try:
            result = onboarding_svc.create_identity(wizard_state)
            wizard_state["creation_result"] = result
        except Exception as e:
            error = f"Failed to create identity: {e}"

    # If error, re-render current step
    if error:
        context = {
            "request": request,
            "csrf_token": request.state.session.get("csrf_token", ""),
            "step": step,
            "total_steps": len(STEPS),
            "step_name": step_name,
            "steps": STEPS,
            "wizard": wizard_state,
            "error": error,
        }
        # Include step-specific data needed for template rendering
        if step_name == "review":
            context["summary"] = _build_summary(wizard_state, request)
        elif step_name == "plugins":
            system_svc = request.app.state.system_service
            context["available_plugins"] = system_svc.get_available_plugins()
            context["capability_bundles"] = system_svc.get_capability_bundles()
        elif step_name == "personality":
            personality_svc = request.app.state.personality_service
            context["available_personalities"] = personality_svc.list_identities()

        template_name = f"onboarding/step{step}_{step_name}.html"
        return templates.TemplateResponse(template_name, context, status_code=400)

    # Persist wizard state server-side and advance to next step
    _set_wizard_state(request, wizard_state)

    next_step = step + 1
    if next_step > len(STEPS):
        # Wizard complete — clean up state
        key = _get_session_key(request)
        _wizard_states.pop(key, None)
        return RedirectResponse("/", status_code=302)

    return RedirectResponse(_step_url(next_step), status_code=302)


@router.post("/onboard/chat")
async def onboard_chat(request: Request):
    """Chat with an identity during onboarding (LLM-powered)."""
    rate_limiter = request.app.state.rate_limiter
    session_key = request.state.session.get("csrf_token", "anon")
    if not rate_limiter.check(f"chat:{session_key}", max_requests=20, window_seconds=600):
        return JSONResponse(
            {"success": False, "error": "Too many requests. Please wait before sending more messages."},
            status_code=429,
        )

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

    # Validate identity name to prevent path traversal
    from overblick.dashboard.routes._plugin_utils import IDENTITY_NAME_RE
    if not IDENTITY_NAME_RE.match(identity_name):
        return JSONResponse(
            {"success": False, "error": "Invalid identity name."},
            status_code=400,
        )

    wizard_state = _get_wizard_state(request)
    llm_config = wizard_state.get("llm", {})

    from overblick.shared.onboarding_chat import chat_with_identity
    result = await chat_with_identity(identity_name, message, llm_config)

    return JSONResponse(result)


@router.post("/onboard/test-llm")
async def onboard_test_llm(request: Request):
    """Test LLM connection during onboarding."""
    wizard_state = _get_wizard_state(request)
    llm_config = wizard_state.get("llm", {})

    if not llm_config:
        return JSONResponse(
            {"success": False, "error": "Configure LLM settings first."},
            status_code=400,
        )

    from overblick.shared.onboarding_chat import test_llm_connection
    result = await test_llm_connection(llm_config)

    return JSONResponse(result)


def _build_summary(wizard_state: dict, request: Request) -> dict:
    """Build a human-readable summary for the review step."""
    return {
        "name": wizard_state.get("name", ""),
        "display_name": wizard_state.get("display_name", ""),
        "description": wizard_state.get("description", ""),
        "personality": wizard_state.get("personality", "none"),
        "llm": wizard_state.get("llm", {}),
        "plugins": wizard_state.get("plugins", []),
        "capabilities": wizard_state.get("capabilities", []),
        "secret_keys": list(wizard_state.get("secrets", {}).keys()),
    }
