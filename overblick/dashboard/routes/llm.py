"""
LLM calls route — view all LLM interactions.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/llm", response_class=HTMLResponse)
async def llm_page(request: Request):
    """Render LLM calls page."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service
    identity_svc = request.app.state.identity_service

    hours = int(request.query_params.get("hours", "24"))
    identity = request.query_params.get("identity", "")

    entries = audit_svc.query(
        category="llm",
        identity=identity,
        since_hours=hours,
        limit=100,
    )

    total_calls = len(entries)
    total_duration = sum(e.get("duration_ms", 0) or 0 for e in entries)
    avg_duration = total_duration / total_calls if total_calls > 0 else 0

    return templates.TemplateResponse("llm.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "entries": entries,
        "identities": identity_svc.list_identities(),
        "selected_identity": identity,
        "selected_hours": hours,
        "total_calls": total_calls,
        "total_duration": total_duration,
        "avg_duration": avg_duration,
    })


@router.get("/partials/llm-table", response_class=HTMLResponse)
async def llm_table_partial(request: Request):
    """htmx partial: filtered LLM calls table."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    hours = int(request.query_params.get("hours", "24"))
    identity = request.query_params.get("identity", "")

    entries = audit_svc.query(
        category="llm",
        identity=identity,
        since_hours=hours,
        limit=100,
    )

    total_calls = len(entries)
    total_duration = sum(e.get("duration_ms", 0) or 0 for e in entries)
    avg_duration = total_duration / total_calls if total_calls > 0 else 0

    return templates.TemplateResponse("partials/llm_table.html", {
        "request": request,
        "entries": entries,
        "total_calls": total_calls,
        "total_duration": total_duration,
        "avg_duration": avg_duration,
    })
