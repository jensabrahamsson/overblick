"""
Audit trail routes — filterable audit log viewer.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..security import AuditFilterForm

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    """Render audit trail page with filters."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service
    identity_svc = request.app.state.identity_service

    # Parse filter params from query string
    params = dict(request.query_params)
    try:
        filters = AuditFilterForm(**params)
    except Exception as e:
        logger.debug("Audit filter validation failed, using defaults: %s", e)
        filters = AuditFilterForm()

    entries = audit_svc.query(
        identity=filters.identity,
        category=filters.category,
        action=filters.action,
        since_hours=filters.hours,
        limit=filters.limit,
    )

    return templates.TemplateResponse("audit.html", {
        "request": request,
        "csrf_token": request.state.session.get("csrf_token", ""),
        "entries": entries,
        "filters": filters,
        "identities": identity_svc.list_identities(),
        "categories": audit_svc.get_categories(),
        "actions": audit_svc.get_actions(),
    })


@router.get("/partials/audit-filtered", response_class=HTMLResponse)
async def audit_filtered_partial(request: Request):
    """htmx partial: filtered audit table."""
    templates = request.app.state.templates
    audit_svc = request.app.state.audit_service

    params = dict(request.query_params)
    try:
        filters = AuditFilterForm(**params)
    except Exception as e:
        logger.debug("Audit filter validation failed, using defaults: %s", e)
        filters = AuditFilterForm()

    entries = audit_svc.query(
        identity=filters.identity,
        category=filters.category,
        action=filters.action,
        since_hours=filters.hours,
        limit=filters.limit,
    )

    return templates.TemplateResponse("partials/audit_table.html", {
        "request": request,
        "entries": entries,
    })
