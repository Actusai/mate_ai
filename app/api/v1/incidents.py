# app/api/v1/incidents.py
from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.incident import Incident
from app.models.ai_system import AISystem

from app.schemas.incident import (
    IncidentCreate,
    IncidentUpdate,
    IncidentOut,
    IncidentStatus,
)

# RBAC / scoping helpers
from app.core.rbac import (
    ensure_company_access,
    ensure_system_access_read,
    ensure_system_write_limited,
    ensure_system_write_full,
)
from app.core.scoping import is_super

# Audit (best-effort)
from app.services.audit import audit_log, audit_export, ip_from_request

# Notifications (best-effort producers)
from app.services.notifications import (
    produce_incident_created,
    produce_incident_status_changed,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _to_out(i: Incident) -> IncidentOut:
    return IncidentOut.model_validate(i)


def _load_system(db: Session, system_id: int) -> AISystem:
    sys = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not sys:
        raise HTTPException(status_code=404, detail="AI system not found")
    return sys


# ---------------------------
# CREATE
# ---------------------------
@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: IncidentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new incident.
    Requires at least limited write access on the target AI system.
    """
    system = _load_system(db, payload.ai_system_id)
    if system.company_id != payload.company_id:
        raise HTTPException(
            status_code=400, detail="company_id does not match AI system"
        )

    # RBAC: limited write is enough to report/create an incident
    ensure_system_write_limited(db, current_user, system.id)

    obj = Incident(
        company_id=payload.company_id,
        ai_system_id=payload.ai_system_id,
        reported_by=getattr(current_user, "id", None),
        occurred_at=payload.occurred_at,
        severity=payload.severity,
        type=payload.type,
        summary=payload.summary,
        details_json=payload.details_json,
        status=payload.status or "new",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=current_user.id,
            action="INCIDENT_CREATED",
            entity_type="incident",
            entity_id=obj.id,
            meta={
                "ai_system_id": obj.ai_system_id,
                "severity": obj.severity,
                "status": obj.status,
                "type": obj.type,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    # NOTIFY (best-effort; do not break main flow)
    try:
        produce_incident_created(
            db,
            incident_id=obj.id,
            company_id=obj.company_id,
            ai_system_id=obj.ai_system_id,
            reported_by=obj.reported_by,
            severity=obj.severity,
            incident_type=obj.type,
            summary=obj.summary,
            occurred_at=obj.occurred_at,
            status=obj.status,  # <-- usklađeno ime argumenta
        )
    except Exception:
        pass

    return _to_out(obj)


# ---------------------------
# READ (by id)
# ---------------------------
@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.query(Incident).filter(Incident.id == incident_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Read access: must be able to read the system
    ensure_system_access_read(db, current_user, obj.ai_system_id)
    return _to_out(obj)


# ---------------------------
# LIST / FILTER
# ---------------------------
@router.get("", response_model=List[IncidentOut])
def list_incidents(
    company_id: Optional[int] = Query(None, description="Scope to a company"),
    ai_system_id: Optional[int] = Query(None, description="Scope to an AI system"),
    status_f: Optional[IncidentStatus] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None, description="Incident type identifier"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Incident)

    # Scoping / RBAC
    if not is_super(current_user):
        # Default scope for non-super if nothing provided
        if company_id is None and ai_system_id is None:
            company_id = getattr(current_user, "company_id", None)

        if company_id is not None:
            ensure_company_access(current_user, company_id)
            q = q.filter(Incident.company_id == company_id)

        if ai_system_id is not None:
            ensure_system_access_read(db, current_user, ai_system_id)
            q = q.filter(Incident.ai_system_id == ai_system_id)

        # If both provided, ensure the system actually belongs to the company (defensive)
        if company_id is not None and ai_system_id is not None:
            sys = _load_system(db, ai_system_id)
            if sys.company_id != company_id:
                # empty result instead of leakage
                return []
    else:
        # SuperAdmin free filters
        if company_id is not None:
            q = q.filter(Incident.company_id == company_id)
        if ai_system_id is not None:
            q = q.filter(Incident.ai_system_id == ai_system_id)

    # Additional filters
    if status_f:
        q = q.filter(Incident.status == status_f)
    if severity:
        q = q.filter(Incident.severity == severity)
    if type:
        q = q.filter(Incident.type == type)
    if date_from:
        q = q.filter(Incident.occurred_at >= date_from)
    if date_to:
        q = q.filter(Incident.occurred_at <= date_to)

    rows = q.order_by(Incident.created_at.desc()).offset(skip).limit(limit).all()
    return [_to_out(r) for r in rows]


# ---------------------------
# UPDATE
# ---------------------------
@router.put("/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: int,
    payload: IncidentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = db.query(Incident).filter(Incident.id == incident_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Needs at least limited write on the system
    ensure_system_write_limited(db, current_user, obj.ai_system_id)

    before_status = obj.status
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(obj, k, v)

    db.add(obj)
    db.commit()
    db.refresh(obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=current_user.id,
            action="INCIDENT_UPDATED",
            entity_type="incident",
            entity_id=obj.id,
            meta={
                "changes": data,
                "ai_system_id": obj.ai_system_id,
                "old_status": before_status,
                "new_status": obj.status,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    # NOTIFY on status change (best-effort)
    try:
        if before_status != obj.status:
            produce_incident_status_changed(
                db,
                incident_id=obj.id,
                company_id=obj.company_id,
                ai_system_id=obj.ai_system_id,
                old_status=before_status or "",
                new_status=obj.status or "",
                severity=obj.severity,
                incident_type=obj.type,
            )
    except Exception:
        pass

    return _to_out(obj)


# ---------------------------
# DELETE
# ---------------------------
@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    obj = db.query(Incident).filter(Incident.id == incident_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Deletion: require full write on the system (same rule as tasks)
    ensure_system_write_full(db, current_user, obj.ai_system_id)

    snapshot = {
        "company_id": obj.company_id,
        "ai_system_id": obj.ai_system_id,
        "severity": obj.severity,
        "type": obj.type,
        "status": obj.status,
        "occurred_at": obj.occurred_at.isoformat() if obj.occurred_at else None,
        "summary": obj.summary,
    }

    db.delete(obj)
    db.commit()

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=snapshot["company_id"],
            user_id=current_user.id,
            action="INCIDENT_DELETED",
            entity_type="incident",
            entity_id=incident_id,
            meta=snapshot,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------
# EXPORT (CSV / JSON / XLSX)
# ---------------------------
@router.get("/export")
def export_incidents(
    request: Request,
    format: str = Query("csv", regex="^(?i)(csv|json|xlsx)$"),
    company_id: Optional[int] = Query(None, description="Scope to company"),
    ai_system_id: Optional[int] = Query(None, description="Scope to AI system"),
    status_f: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None, description="Incident type"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(20000, ge=1, le=200000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # RBAC scope:
    if not is_super(current_user):
        if company_id is None:
            company_id = getattr(current_user, "company_id", None)
        if company_id is None:
            raise HTTPException(
                status_code=403, detail="Company scope required for non-super users"
            )
        ensure_company_access(current_user, company_id)

    # Build query
    q = db.query(Incident)
    if company_id is not None:
        q = q.filter(Incident.company_id == company_id)
    if ai_system_id is not None:
        # guard even for super: enforce system read if provided
        ensure_system_access_read(db, current_user, ai_system_id)
        q = q.filter(Incident.ai_system_id == ai_system_id)
    if status_f:
        q = q.filter(Incident.status == status_f)
    if severity:
        q = q.filter(Incident.severity == severity)
    if type:
        q = q.filter(Incident.type == type)
    if date_from:
        q = q.filter(Incident.occurred_at >= date_from)
    if date_to:
        q = q.filter(Incident.occurred_at <= date_to)

    rows: List[Incident] = q.order_by(Incident.created_at.desc()).limit(limit).all()
    if not rows:
        raise HTTPException(
            status_code=404, detail="No data found for the given parameters"
        )

    # Shape rows
    def rowdict(x: Incident) -> Dict[str, Any]:
        return {
            "id": x.id,
            "company_id": x.company_id,
            "ai_system_id": x.ai_system_id,
            "reported_by": x.reported_by,
            "occurred_at": x.occurred_at.isoformat() if x.occurred_at else None,
            "severity": x.severity,
            "type": x.type,
            "summary": x.summary,
            "details_json": x.details_json,
            "status": x.status,
            "created_at": x.created_at.isoformat() if x.created_at else None,
            "updated_at": x.updated_at.isoformat() if x.updated_at else None,
        }

    data = [rowdict(r) for r in rows]
    fmt = format.lower()

    # JSON
    if fmt == "json":
        payload = {"items": data, "count": len(data)}
        # audit (best-effort)
        try:
            audit_export(
                db,
                company_id=(company_id or getattr(current_user, "company_id", 0) or 0),
                user_id=getattr(current_user, "id", None),
                export_type="incidents:json",
                table_or_view="incidents",
                row_count=len(data),
                ip=ip_from_request(request),
                extras={
                    "ai_system_id": ai_system_id,
                    "status": status_f,
                    "severity": severity,
                    "type": type,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "limit": limit,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        return JSONResponse(content=payload)

    # XLSX
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except Exception:
            raise HTTPException(
                status_code=400, detail="XLSX export requires 'openpyxl' package."
            )

        wb = Workbook()
        ws = wb.active
        ws.title = "incidents"
        cols = list(data[0].keys())
        ws.append(cols)
        for r in data:
            ws.append([r.get(k) for k in cols])

        import io

        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        # audit (best-effort)
        try:
            audit_export(
                db,
                company_id=(company_id or getattr(current_user, "company_id", 0) or 0),
                user_id=getattr(current_user, "id", None),
                export_type="incidents:xlsx",
                table_or_view="incidents",
                row_count=len(data),
                ip=ip_from_request(request),
                extras={
                    "ai_system_id": ai_system_id,
                    "status": status_f,
                    "severity": severity,
                    "type": type,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "limit": limit,
                },
            )
            db.commit()
        except Exception:
            db.rollback()

        headers = {"Content-Disposition": "attachment; filename=incidents.xlsx"}
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    # CSV (default)
    import io, csv

    out = io.StringIO()
    cols = list(data[0].keys())
    writer = csv.DictWriter(out, fieldnames=cols)
    writer.writeheader()
    for r in data:
        writer.writerow(r)
    out.seek(0)

    # audit (best-effort)
    try:
        audit_export(
            db,
            company_id=(company_id or getattr(current_user, "company_id", 0) or 0),
            user_id=getattr(current_user, "id", None),
            export_type="incidents:csv",
            table_or_view="incidents",
            row_count=len(data),
            ip=ip_from_request(request),
            extras={
                "ai_system_id": ai_system_id,
                "status": status_f,
                "severity": severity,
                "type": type,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "limit": limit,
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    headers = {"Content-Disposition": "attachment; filename=incidents.csv"}
    return StreamingResponse(out, media_type="text/csv; charset=utf-8", headers=headers)
