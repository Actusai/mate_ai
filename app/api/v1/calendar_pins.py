# app/api/v1/calendar_pins.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.auth import get_db, get_current_user
from app.core.rbac import ensure_company_access_strict
from app.core.scoping import (
    is_super,
    is_staff_admin,
    can_write_company,
    is_assigned_admin,
)
from app.models.user import User
from app.schemas.calendar_pin import (
    CalendarPinCreate,
    CalendarPinUpdate,
    CalendarPinOut,
)

# audit (best-effort)
try:
    from app.services.audit import audit_log, ip_from_request
except Exception:  # pragma: no cover
    audit_log = None  # type: ignore

    def ip_from_request(_req):  # type: ignore
        return None


router = APIRouter(prefix="/calendar/pins", tags=["calendar"])

# ---------------------------
# Helpers
# ---------------------------
_PIN_COLUMNS = (
    "id, company_id, ai_system_id, title, description, start_at, end_at, "
    "visibility, severity, status, created_by_user_id, updated_by_user_id, "
    "created_at, updated_at"
)


def _row_to_out(row: Dict[str, Any]) -> CalendarPinOut:
    # Pydantic v2 će pretvoriti ISO stringove u datetime objekte
    return CalendarPinOut.model_validate(dict(row))


def _parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _can_view_internal(db: Session, user: User, company_id: int) -> bool:
    """Internal pins are visible to SuperAdmin and Staff Admin assigned to that company."""
    if is_super(user):
        return True
    if is_staff_admin(user) and is_assigned_admin(db, user, company_id):
        return True
    return False


def _can_write_pin(
    db: Session, user: User, company_id: int, target_visibility: str
) -> bool:
    """
    Write rules:
      - visibility='mate_internal' → SuperAdmin or assigned Staff Admin
      - visibility='company'       → Client Admin (company write), or assigned Staff Admin, or SuperAdmin
    """
    if target_visibility == "mate_internal":
        return _can_view_internal(db, user, company_id)
    # 'company'
    if is_super(user):
        return True
    if is_staff_admin(user) and is_assigned_admin(db, user, company_id):
        return True
    return can_write_company(db, user, company_id)


def _fetch_pin(db: Session, pin_id: int) -> Optional[Dict[str, Any]]:
    row = (
        db.execute(
            text(f"SELECT {_PIN_COLUMNS} FROM calendar_pins WHERE id = :id LIMIT 1"),
            {"id": pin_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


# ---------------------------
# CREATE
# ---------------------------
@router.post("", response_model=CalendarPinOut, status_code=status.HTTP_201_CREATED)
def create_calendar_pin(
    payload: CalendarPinCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a calendar pin (milestone/event).
    - visibility='company' → requires company write (Client Admin) OR assigned Staff Admin OR SuperAdmin
    - visibility='mate_internal' → only SuperAdmin or assigned Staff Admin
    """
    ensure_company_access_strict(current_user, payload.company_id, db)

    if not _can_write_pin(db, current_user, payload.company_id, payload.visibility):
        raise HTTPException(
            status_code=403, detail="Insufficient privileges to create this pin."
        )

    data = payload.model_dump(exclude_none=True)

    # Consistency check for times if both present (schemas već rade parsing, ali dodatan guard)
    start_at = data.get("start_at")
    end_at = data.get("end_at")
    if start_at and end_at:
        if str(end_at) < str(start_at):
            raise HTTPException(
                status_code=400,
                detail="end_at must be greater than or equal to start_at.",
            )

    # Insert (SQLite-friendly timestamps)
    sql = text(
        """
        INSERT INTO calendar_pins (
            company_id, ai_system_id, title, description,
            start_at, end_at, visibility, severity, status,
            created_by_user_id, updated_by_user_id, created_at, updated_at
        ) VALUES (
            :company_id, :ai_system_id, :title, :description,
            :start_at, :end_at, :visibility, :severity, :status,
            :uid, :uid, datetime('now'), datetime('now')
        )
        """
    )
    db.execute(
        sql,
        {
            "company_id": data["company_id"],
            "ai_system_id": data.get("ai_system_id"),
            "title": data["title"],
            "description": data.get("description"),
            "start_at": data["start_at"],
            "end_at": data.get("end_at"),
            "visibility": data["visibility"],
            "severity": data.get("severity"),
            "status": data.get("status") or "active",
            "uid": getattr(current_user, "id", None),
        },
    )
    # fetch last row (SQLite-safe)
    new_id = (
        db.execute(text("SELECT last_insert_rowid() AS id")).mappings().first()["id"]
    )
    db.commit()

    row = _fetch_pin(db, int(new_id))
    if not row:
        raise HTTPException(status_code=500, detail="Failed to load newly created pin.")

    # AUDIT (best-effort)
    try:
        if callable(audit_log):
            audit_log(
                db,
                company_id=row["company_id"],
                user_id=getattr(current_user, "id", None),
                action="CAL_PIN_CREATED",
                entity_type="calendar_pin",
                entity_id=row["id"],
                meta={"visibility": row["visibility"], "title": row["title"]},
                ip=ip_from_request(request),
            )
            db.commit()
    except Exception:
        db.rollback()

    return _row_to_out(row)


# ---------------------------
# LIST
# ---------------------------
@router.get("", response_model=List[CalendarPinOut])
def list_calendar_pins(
    company_id: int = Query(..., ge=1),
    ai_system_id: Optional[int] = Query(None, ge=1),
    visibility: Optional[str] = Query(None, regex="^(company|mate_internal)$"),
    dt_from: Optional[str] = Query(
        None, description="Filter: start_at >= this ISO datetime"
    ),
    dt_to: Optional[str] = Query(
        None, description="Filter: start_at <= this ISO datetime"
    ),
    status_filter: Optional[str] = Query(
        None, description="Filter by status (e.g. active)"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List calendar pins for a company.
    Visibility rules:
      - Company users (client admin/members): only 'company' pins.
      - Staff Admin: 'company' pins always; 'mate_internal' only if assigned to that company.
      - SuperAdmin: both.
    """
    ensure_company_access_strict(current_user, company_id, db)

    # Decide allowed visibilities for viewer
    allowed_vis = {"company"}
    if _can_view_internal(db, current_user, company_id):
        allowed_vis.add("mate_internal")

    # If caller asked for a particular visibility, constrain to intersection
    if visibility:
        if visibility not in allowed_vis:
            # pretend empty (no access to that scope)
            return []
        allowed_vis = {visibility}

    filters = ["company_id = :cid"]
    params: Dict[str, Any] = {"cid": company_id}

    # Build visibility IN clause (1 ili 2 vrijednosti)
    vis_list = sorted(list(allowed_vis))
    if len(vis_list) == 1:
        filters.append("visibility IN (:v1)")
        params["v1"] = vis_list[0]
    else:
        filters.append("visibility IN (:v1, :v2)")
        params["v1"], params["v2"] = vis_list[0], vis_list[1]

    if ai_system_id is not None:
        filters.append("ai_system_id = :aid")
        params["aid"] = ai_system_id

    if status_filter:
        filters.append("status = :st")
        params["st"] = status_filter

    f_dt = _parse_iso_dt(dt_from)
    t_dt = _parse_iso_dt(dt_to)
    if f_dt:
        filters.append("start_at >= :from_dt")
        params["from_dt"] = f_dt.isoformat()
    if t_dt:
        filters.append("start_at <= :to_dt")
        params["to_dt"] = t_dt.isoformat()

    rows = (
        db.execute(
            text(
                f"""
            SELECT {_PIN_COLUMNS}
            FROM calendar_pins
            WHERE {" AND ".join(filters)}
            ORDER BY start_at ASC, id ASC
            LIMIT :lim OFFSET :off
            """
            ),
            {**params, "lim": limit, "off": skip},
        )
        .mappings()
        .all()
    )

    return [_row_to_out(dict(r)) for r in rows]


# ---------------------------
# GET ONE
# ---------------------------
@router.get("/{pin_id}", response_model=CalendarPinOut)
def get_calendar_pin(
    pin_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = _fetch_pin(db, pin_id)
    if not row:
        raise HTTPException(status_code=404, detail="Calendar pin not found")

    ensure_company_access_strict(current_user, int(row["company_id"]), db)

    # Check view permission for internal
    if row["visibility"] == "mate_internal" and not _can_view_internal(
        db, current_user, int(row["company_id"])
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    return _row_to_out(row)


# ---------------------------
# UPDATE (partial)
# ---------------------------
@router.patch("/{pin_id}", response_model=CalendarPinOut)
def update_calendar_pin(
    pin_id: int,
    payload: CalendarPinUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = _fetch_pin(db, pin_id)
    if not row:
        raise HTTPException(status_code=404, detail="Calendar pin not found")

    company_id = int(row["company_id"])
    ensure_company_access_strict(current_user, company_id, db)

    # Determine target visibility (if changed)
    target_visibility = payload.visibility or row["visibility"]

    if not _can_write_pin(db, current_user, company_id, target_visibility):
        raise HTTPException(
            status_code=403, detail="Insufficient privileges to update this pin."
        )

    data = payload.model_dump(exclude_none=True)
    if not data:
        return _row_to_out(row)  # nothing to change

    # If both start_at and end_at provided, guard ordering
    start_at_new = data.get("start_at", row["start_at"])
    end_at_new = data.get("end_at", row["end_at"])
    if start_at_new and end_at_new and str(end_at_new) < str(start_at_new):
        raise HTTPException(
            status_code=400, detail="end_at must be greater than or equal to start_at."
        )

    # Build dynamic SET clause
    set_cols = []
    params: Dict[str, Any] = {"id": pin_id, "uid": getattr(current_user, "id", None)}
    for key in (
        "ai_system_id",
        "title",
        "description",
        "start_at",
        "end_at",
        "visibility",
        "severity",
        "status",
    ):
        if key in data:
            set_cols.append(f"{key} = :{key}")
            params[key] = data[key]
    set_cols.append("updated_by_user_id = :uid")
    set_cols.append("updated_at = datetime('now')")

    db.execute(
        text(f"UPDATE calendar_pins SET {', '.join(set_cols)} WHERE id = :id"),
        params,
    )
    db.commit()

    row2 = _fetch_pin(db, pin_id)
    if not row2:
        raise HTTPException(
            status_code=500, detail="Failed to reload calendar pin after update."
        )

    # AUDIT (best-effort)
    try:
        if callable(audit_log):
            audit_log(
                db,
                company_id=company_id,
                user_id=getattr(current_user, "id", None),
                action="CAL_PIN_UPDATED",
                entity_type="calendar_pin",
                entity_id=pin_id,
                meta={"changes": list(data.keys())},
                ip=ip_from_request(request),
            )
            db.commit()
    except Exception:
        db.rollback()

    return _row_to_out(row2)


# ---------------------------
# DELETE
# ---------------------------
@router.delete("/{pin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_calendar_pin(
    pin_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    row = _fetch_pin(db, pin_id)
    if not row:
        raise HTTPException(status_code=404, detail="Calendar pin not found")

    company_id = int(row["company_id"])
    ensure_company_access_strict(current_user, company_id, db)

    if not _can_write_pin(db, current_user, company_id, row["visibility"]):
        raise HTTPException(
            status_code=403, detail="Insufficient privileges to delete this pin."
        )

    db.execute(text("DELETE FROM calendar_pins WHERE id = :id"), {"id": pin_id})
    db.commit()

    # AUDIT (best-effort)
    try:
        if callable(audit_log):
            audit_log(
                db,
                company_id=company_id,
                user_id=getattr(current_user, "id", None),
                action="CAL_PIN_DELETED",
                entity_type="calendar_pin",
                entity_id=pin_id,
                meta={"visibility": row["visibility"], "title": row["title"]},
                ip=ip_from_request(request),
            )
            db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
