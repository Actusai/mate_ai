# app/api/v1/calendar.py
from __future__ import annotations
from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.company import Company

# RBAC: prefer strict access; fall back to the looser guard if strict is unavailable
try:
    from app.core.rbac import ensure_company_access_strict as _ensure_company_access
except Exception:  # pragma: no cover
    from app.core.rbac import ensure_company_access as _ensure_company_access  # type: ignore

# Who can see internal pins?
from app.core.scoping import is_staff_admin, is_super

from app.services.calendar import (
    collect_company_events,
    build_company_calendar_ics,
    verify_ics_token,
    _validate_visibility,  # kept for potential future CRUD use
)

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/company/{company_id}/events")
def company_events(
    company_id: int,
    dt_from: Optional[str] = Query(None, description="ISO date/datetime (UTC)"),
    dt_to: Optional[str] = Query(None, description="ISO date/datetime (UTC)"),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """
    Returns JSON list of calendar events for a company.

    Includes:
      - open compliance tasks (due),
      - regulatory deadlines,
      - document reviews,
      - (optional) compliance_due_dates,
      - (optional) manual calendar pins.

    RBAC:
      - strict company access (Client Admin / same-company users, Staff Admin assigned, SuperAdmin assigned).
      - Internal pins (visibility='mate_internal') are shown only to assigned Staff Admins or Super Admins.
    """
    _ensure_company_access(current_user, company_id, db)  # strict if available

    include_internal = bool(is_staff_admin(current_user) or is_super(current_user))
    # service tolerates dt_from/dt_to as ISO strings and will parse them safely
    return collect_company_events(  # type: ignore[arg-type]
        db,
        company_id,
        dt_from=dt_from,
        dt_to=dt_to,
        limit=limit,
        viewer=current_user,
        include_internal_for_viewer=include_internal,
    )


@router.get("/company/{company_id}.ics", response_class=PlainTextResponse)
def company_calendar_ics(
    company_id: int,
    token: str = Query(
        ..., description="Read-only token (sha256(APP_SECRET:company_id)[:32])"
    ),
    dt_from: Optional[str] = Query(None),
    dt_to: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """
    Read-only ICS feed for company calendar (no auth cookie/bearer).
    Security: requires a valid 'token'.

    Notes:
      - Internal pins (visibility='mate_internal') are never included in the ICS feed.
    """
    if not verify_ics_token(token, company_id):
        raise HTTPException(status_code=403, detail="Invalid or missing token")

    events = collect_company_events(  # type: ignore[arg-type]
        db,
        company_id,
        dt_from=dt_from,
        dt_to=dt_to,
        limit=limit,
        viewer=None,
        include_internal_for_viewer=False,  # ICS never exposes internal pins
    )

    company = db.query(Company).filter(Company.id == company_id).first()
    company_name = (
        getattr(company, "name", f"Company {company_id}")
        if company
        else f"Company {company_id}"
    )

    ics = build_company_calendar_ics(company_name, events)
    return PlainTextResponse(content=ics, media_type="text/calendar; charset=utf-8")
