# app/api/v1/notifications.py
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.core.rbac import is_super_admin, ensure_company_access
from app.services.notifications import run_notifications_cycle
from app.services.audit import audit_log, ip_from_request
import json

router = APIRouter()

def _derive_subject(n: Dict[str, Any]) -> Optional[str]:
    """
    Lagan subject za prikaz u UI-u, izveden iz type/payload.
    """
    t = (n.get("type") or "").lower()
    payload = n.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    if t == "task_due_soon":
        title = payload.get("title") or payload.get("task_title") or "Compliance task"
        return f"[Reminder] {title}"
    # fallback
    return None

@router.get("/notifications")
def list_notifications(
    status: Optional[str] = Query(None, pattern="^(queued|sent|failed)$"),
    company_id: Optional[int] = Query(None, description="SuperAdmin može zadati; ostali ignorira se"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    # Odredi company scope
    if is_super_admin(current_user) and company_id is not None:
        cid = company_id
    else:
        cid = getattr(current_user, "company_id", None)
    if cid is None:
        raise HTTPException(status_code=403, detail="Company scope missing")

    where = ["company_id = :cid"]
    params = {"cid": cid}
    if status:
        where.append("LOWER(status) = :st")
        params["st"] = status.lower()

    rows = db.execute(
        text(f"""
            SELECT id, company_id, user_id, ai_system_id, task_id,
                   type, channel, payload, status, error, scheduled_at, sent_at, created_at
            FROM notifications
            WHERE {" AND ".join(where)}
            ORDER BY id DESC
            LIMIT :lim
        """),
        {**params, "lim": limit},
    ).mappings().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        # pokušaj JSON decode-a payload-a
        raw_payload = item.get("payload")
        if isinstance(raw_payload, str):
            try:
                item["payload"] = json.loads(raw_payload)
            except Exception:
                # ostavi string ako nije JSON
                pass
        # praktičan subject
        item["subject"] = _derive_subject(item)
        out.append(item)

    return out


@router.post("/notifications/run")
def trigger_notifications_run(
    request: Request,
    company_id: Optional[int] = Query(None, description="Ako je zadano i korisnik je SuperAdmin, pokreni za tu firmu"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # RBAC: ako je zadan company_id, superadmin smije sve; non-super mora imati pristup toj firmi
    if company_id is not None:
        if not is_super_admin(current_user):
            ensure_company_access(current_user, company_id)
        target_cid = company_id
    else:
        # default: userova firma (superadmin može None = sve firme)
        target_cid = None if is_super_admin(current_user) else getattr(current_user, "company_id", None)
        if target_cid is None and not is_super_admin(current_user):
            raise HTTPException(status_code=403, detail="Company scope missing")

    res = run_notifications_cycle(db, company_id=target_cid)
    # poslovna operacija je već commit-ana u servisu; ovdje best-effort audit
    try:
        audit_log(
            db,
            company_id=(target_cid or getattr(current_user, "company_id", 0) or 0),
            user_id=getattr(current_user, "id", None),
            action="NOTIFICATIONS_CYCLE_TRIGGERED",
            entity_type="notification",
            entity_id=None,
            meta={"company_id": target_cid, **res},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, **res}