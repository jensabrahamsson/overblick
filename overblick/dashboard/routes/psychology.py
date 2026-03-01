"""
Psychology hub — overview of personality analysis plugins.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

# Plugin metadata for the hub page
_PSYCHOLOGY_PLUGINS = [
    {
        "name": "spegel",
        "display_name": "Spegel",
        "subtitle": "Inter-Agent Profiling",
        "description": (
            "Agents write psychological profiles of each other, then reflect on "
            "how they are perceived. Reveals blind spots and hidden dynamics "
            "between personalities."
        ),
        "url": "/spegel",
        "icon": "🪞",
    },
    {
        "name": "skuggspel",
        "display_name": "Skuggspel",
        "subtitle": "Shadow Self Generation",
        "description": (
            "Inspired by Jungian shadow theory. Each identity generates content "
            "from their psychological opposite — the parts of themselves they "
            "normally suppress."
        ),
        "url": "/skuggspel",
        "icon": "🌑",
    },
    {
        "name": "kontrast",
        "display_name": "Kontrast",
        "subtitle": "Multi-Perspective Commentary",
        "description": (
            "All identities write their take on the same topic simultaneously. "
            "Published side-by-side, revealing how different personalities "
            "interpret the same reality."
        ),
        "url": "/kontrast",
        "icon": "🔀",
    },
    {
        "name": "compass",
        "display_name": "Compass",
        "subtitle": "Identity Drift Detection",
        "description": (
            "Monitors agent outputs over time using stylometric analysis. "
            "Catches personality flattening, trait drift, and potential "
            "prompt injection attempts."
        ),
        "url": "/compass",
        "icon": "🧭",
    },
    {
        "name": "stage",
        "display_name": "Stage",
        "subtitle": "Behavioral Scenario Testing",
        "description": (
            "YAML-driven test scenarios that validate identity behavior against "
            "defined constraints. Ensures personalities stay true under pressure."
        ),
        "url": "/stage",
        "icon": "🎭",
    },
]


def _check_plugin_enabled(identities: list[dict], plugin_name: str) -> bool:
    """Check if any identity has this plugin configured."""
    return any(
        plugin_name in identity.get("plugins", [])
        for identity in identities
    )


@router.get("/psychology", response_class=HTMLResponse)
async def psychology_hub(request: Request):
    """Render the psychology plugins hub page."""
    templates = request.app.state.templates
    identity_svc = request.app.state.identity_service
    identities = identity_svc.get_all_identities()

    plugins = []
    for plugin in _PSYCHOLOGY_PLUGINS:
        plugins.append({
            **plugin,
            "enabled": _check_plugin_enabled(identities, plugin["name"]),
        })

    return templates.TemplateResponse("psychology.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "plugins": plugins,
    })
