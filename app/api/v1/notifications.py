# app/api/v1/notifications.py
from __future__ import annotations

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.core.scoping import is_super

from app.services.notifications import (
    # producers
    generate_due_task_reminders,
    generate_stale_evidence_reminders,
    generate_regulatory_deadline_reminders,
    generate_compliance_due_reminders,
    generate_assessment_version_notifications,
    generate_incident_recent_notifications,
    # cycle + sender
    run_notifications_cycle,
    send_pending_notifications,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _ensure_superadmin(user: User) -> None:
    if not is_super(user):
        raise HTTPException(status_code=403, detail="Super Admin only")


# -----------------------------
# LIST notifications (with filters)
# -----------------------------
@router.get("")
def list_notifications(
    type: Optional[List[str]] = Query(None, description="Filter by notification type (repeatable)"),
    status: Optional[List[str]] = Query(None, description="Filter by status (queued|sent|failed; repeatable)"),
    ai_system_id: Optional[int] = Query(None, ge=1),
    company_id: Optional[int] = Query(
        None,
        ge=1,
        description="Super Admin only: view notifications for a specific company",
    ),
    mine_only: bool = Query(False, description="If true (non-super), return only notifications addressed to the current user_id"),
    created_from: Optional[str] = Query(None, description="ISO date/datetime lower bound for created_at"),
    created_to: Optional[str] = Query(None, description="ISO date/datetime upper bound for created_at"),
    order_by: str = Query("created_at", pattern="^(id|created_at|sent_at)$"),
    order_dir: str = Query("desc", pattern="^(?i)(asc|desc)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Returns notifications with flexible filtering. Non–Super Admin users are scoped to their company.
    """
    filters: List[str] = []
    params: Dict[str, Any] = {}

    # Scoping
    if is_super(current_user):
        if company_id is not None:
            filters.append("company_id = :cid")
            params["cid"] = company_id
    else:
        if not current_user.company_id:
            return {"items": [], "count": 0}
        filters.append("company_id = :cid")
        params["cid"] = current_user.company_id
        if mine_only:
            filters.append("(user_id = :uid OR user_id IS NULL)")
            params["uid"] = current_user.id

    # Filters
    if ai_system_id is not None:
        filters.append("ai_system_id = :aid")
        params["aid"] = ai_system_id

    if type:
        type = [t.strip() for t in type if t and t.strip()]
        if type:
            placeholders = ", ".join(f":t{i}" for i in range(len(type)))
            filters.append(f"type IN ({placeholders})")
            for i, v in enumerate(type):
                params[f"t{i}"] = v

    if status:
        status = [s.strip().lower() for s in status if s and s.strip()]
        if status:
            placeholders = ", ".join(f":s{i}" for i in range(len(status)))
            filters.append(f"LOWER(status) IN ({placeholders})")
            for i, v in enumerate(status):
                params[f"s{i}"] = v

    if created_from:
        filters.append("created_at >= :cfrom")
        params["cfrom"] = created_from
    if created_to:
        filters.append("created_at <= :cto")
        params["cto"] = created_to

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    order = f"{order_by} {order_dir.upper()}"
    base_select = """
        SELECT id, company_id, user_id, ai_system_id, task_id,
               type, channel, status, error, payload, scheduled_at, sent_at, created_at
        FROM notifications
    """

    # Count
    cnt_row = db.execute(text(f"SELECT COUNT(1) AS c FROM notifications {where}"), params).mappings().first()
    total = int(cnt_row["c"] if cnt_row and "c" in cnt_row else 0)

    rows = db.execute(
        text(f"{base_select} {where} ORDER BY {order} LIMIT :lim OFFSET :off"),
        {**params, "lim": limit, "off": offset},
    ).mappings().all()

    def _coerce_payload(v: Any) -> Any:
        if v is None:
            return None
        try:
            return json.loads(v)
        except Exception:
            return v

    items = []
    for r in rows:
        d = dict(r)
        d["payload"] = _coerce_payload(d.get("payload"))
        items.append(d)

    return {"items": items, "count": total}


# -----------------------------
# GET single notification
# -----------------------------
@router.get("/{notification_id}")
def get_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Fetch a single notification by ID (scoped to the user's company unless Super Admin).
    """
    row = db.execute(
        text(
            """
            SELECT id, company_id, user_id, ai_system_id, task_id,
                   type, channel, status, error, payload, scheduled_at, sent_at, created_at
            FROM notifications
            WHERE id = :nid
            """
        ),
        {"nid": notification_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")

    if not is_super(current_user):
        if int(row["company_id"]) != int(getattr(current_user, "company_id", 0) or 0):
            raise HTTPException(status_code=403, detail="Forbidden")

    out = dict(row)
    try:
        out["payload"] = json.loads(out.get("payload") or "{}")
    except Exception:
        pass
    return out


# -----------------------------
# ADMIN: targeted producers
# -----------------------------
@router.post("/admin/run-deadline-reminders")
def admin_run_deadline_reminders(
    company_id: Optional[int] = Query(None, ge=1, description="Limit to a single company"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate and send reminders for regulatory deadlines and high-level compliance due dates.
    Super Admin only.
    """
    _ensure_superadmin(current_user)

    created_reg = generate_regulatory_deadline_reminders(db, for_company_id=company_id)
    created_comp = generate_compliance_due_reminders(db, for_company_id=company_id)
    sent = send_pending_notifications(db, for_company_id=company_id)

    return {
        "ok": True,
        "created": {
            "regulatory_deadlines": created_reg,
            "compliance_due": created_comp,
        },
        "sent": sent,
    }


@router.post("/admin/run-task-reminders")
def admin_run_task_reminders(
    company_id: Optional[int] = Query(None, ge=1, description="Limit to a single company"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate and send reminders for compliance tasks (due soon / overdue).
    Super Admin only.
    """
    _ensure_superadmin(current_user)

    created = generate_due_task_reminders(db, for_company_id=company_id)
    sent = send_pending_notifications(db, for_company_id=company_id)
    return {"ok": True, "created": {"task_due_soon": created}, "sent": sent}


@router.post("/admin/run-stale-evidence-reminders")
def admin_run_stale_evidence_reminders(
    company_id: Optional[int] = Query(None, ge=1, description="Limit to a single company"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate and send reminders for documents with review_due_at in the past.
    Super Admin only.
    """
    _ensure_superadmin(current_user)

    created = generate_stale_evidence_reminders(db, for_company_id=company_id)
    sent = send_pending_notifications(db, for_company_id=company_id)
    return {"ok": True, "created": {"stale_evidence": created}, "sent": sent}


# -----------------------------
# ADMIN: unified full-cycle
# -----------------------------
@router.post("/admin/run-cycle")
def admin_run_all_notifications_cycle(
    company_id: Optional[int] = Query(None, ge=1, description="Limit to a single company"),
    scan_hours: int = Query(24, ge=1, le=168, description="Window for scanning new assessment versions/incidents"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Run all producers (tasks, stale evidence, regulatory deadlines, compliance due, assessment versions, recent incidents)
    and send queued notifications. Super Admin only.
    """
    _ensure_superadmin(current_user)
    res = run_notifications_cycle(db, company_id=company_id, scan_hours=scan_hours)
    return {"ok": True, **res}