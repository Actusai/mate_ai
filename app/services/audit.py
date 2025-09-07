# app/services/audit.py
from __future__ import annotations
from typing import Any, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import text
import json as _json

# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------

def ip_from_request(request) -> Optional[str]:
    """
    Extract client IP:
      - X-Forwarded-For (prvi IP)
      - X-Real-IP
      - request.client.host
    """
    try:
        if not request:
            return None
        xf = request.headers.get("x-forwarded-for")
        if xf:
            ip = xf.split(",")[0].strip()
        else:
            ip = request.headers.get("x-real-ip") or (request.client.host if request.client else None)
        return ip[:45] if ip else None
    except Exception:
        return None


def json_dump(obj: Dict[str, Any]) -> str:
    """Safe JSON dump (compact, UTF-8, no exceptions)."""
    try:
        return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _sanitize(action: str, entity_type: Optional[str], ip: Optional[str]) -> tuple[str, Optional[str], Optional[str]]:
    action_s = (action or "").upper()[:100]
    entity_type_s = (entity_type[:50] if entity_type else None)
    ip_s = (ip[:45] if ip else None)
    return action_s, entity_type_s, ip_s


# -------------------------------------------------------------------
# Core audit API
# -------------------------------------------------------------------

def audit_log(
    db: Session,
    *,
    company_id: int,
    user_id: Optional[int],
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> None:
    """
    Zapiši događaj u audit_logs. Ne radi commit — call-site odlučuje.
    """
    action_s, entity_type_s, ip_s = _sanitize(action, entity_type, ip)

    db.execute(
        text("""
            INSERT INTO audit_logs (
                company_id, user_id, action, entity_type, entity_id, meta, ip_address, created_at
            )
            VALUES (:company_id, :user_id, :action, :entity_type, :entity_id, :meta, :ip, datetime('now'))
        """),
        {
            "company_id": company_id,
            "user_id": user_id,
            "action": action_s,
            "entity_type": entity_type_s,
            "entity_id": entity_id,
            "meta": (None if meta is None else json_dump(meta)),
            "ip": ip_s,
        },
    )


def audit_export(
    db: Session,
    *,
    company_id: int,
    user_id: Optional[int],
    export_type: str,
    table_or_view: str,
    row_count: int,
    ip: Optional[str],
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Prečica za export događaje.
    """
    meta = {"table_or_view": table_or_view, "row_count": row_count}
    if extras:
        meta.update(extras)
    audit_log(
        db,
        company_id=company_id,
        user_id=user_id,
        action="EXPORT_PERFORMED",
        entity_type="export",
        entity_id=None,
        meta={"type": export_type, **meta},
        ip=ip,
    )


# -------------------------------------------------------------------
# Optional convenience helpers (ne mijenjaju postojeće ponašanje)
# -------------------------------------------------------------------

def audit_login_success(db: Session, *, company_id: int, user_id: int, ip: Optional[str]) -> None:
    audit_log(
        db,
        company_id=company_id,
        user_id=user_id,
        action="LOGIN_SUCCESS",
        entity_type="auth",
        entity_id=user_id,
        meta=None,
        ip=ip,
    )

def audit_login_failed(db: Session, *, company_id: int, user_email: str, ip: Optional[str]) -> None:
    audit_log(
        db,
        company_id=company_id,
        user_id=None,
        action="LOGIN_FAILED",
        entity_type="auth",
        entity_id=None,
        meta={"email": user_email},
        ip=ip,
    )

def audit_system_assignment(db: Session, *, company_id: int, actor_user_id: int, ai_system_id: int, target_user_id: int, action: str, ip: Optional[str]) -> None:
    """
    action: 'SYSTEM_ASSIGNMENT_CREATED' | 'SYSTEM_ASSIGNMENT_DELETED'
    """
    audit_log(
        db,
        company_id=company_id,
        user_id=actor_user_id,
        action=action,
        entity_type="system_assignment",
        entity_id=None,
        meta={"ai_system_id": ai_system_id, "target_user_id": target_user_id},
        ip=ip,
    )

def audit_task_change(db: Session, *, company_id: int, actor_user_id: int, task_id: int, action: str, meta: Optional[Dict[str, Any]], ip: Optional[str]) -> None:
    """
    action: 'TASK_CREATED' | 'TASK_UPDATED' | 'TASK_DELETED'
    """
    audit_log(
        db,
        company_id=company_id,
        user_id=actor_user_id,
        action=action,
        entity_type="compliance_task",
        entity_id=task_id,
        meta=meta or {},
        ip=ip,
    )