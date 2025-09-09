# app/services/audit.py
from __future__ import annotations

import json
from typing import Any, Optional, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi import Request


# -----------------------------
# Helpers
# -----------------------------
def ip_from_request(request: Optional[Request]) -> Optional[str]:
    """
    Best-effort client IP extraction compatible with proxies.
    """
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # take the first IP in the chain
        return xff.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def _ensure_audit_table(db: Session) -> None:
    """
    Creates a very simple audit_logs table if it doesn't exist yet.
    Safe to call repeatedly; keeps you running even without Alembic.
    """
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NULL,
                user_id INTEGER NULL,
                action TEXT NOT NULL,
                entity_type TEXT NULL,
                entity_id INTEGER NULL,
                meta TEXT NULL,
                ip_address TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    )
    db.commit()


# -----------------------------
# Core API
# -----------------------------
def audit_log(
    db: Session,
    *,
    company_id: Optional[int],
    user_id: Optional[int],
    action: str,
    entity_type: Optional[str],
    entity_id: Optional[int],
    meta: Optional[Dict[str, Any]],
    ip: Optional[str],
) -> None:
    """
    Inserts an audit record. Falls back to creating the table if missing.
    Never raises (swallows failures purposely to not break main flow).
    """
    try:
        payload = json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":"))
        db.execute(
            text(
                """
                INSERT INTO audit_logs (
                    company_id, user_id, action, entity_type, entity_id, meta, ip_address, created_at
                ) VALUES (
                    :company_id, :user_id, :action, :entity_type, :entity_id, :meta, :ip, datetime('now')
                )
                """
            ),
            {
                "company_id": company_id,
                "user_id": user_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "meta": payload,
                "ip": ip,
            },
        )
        db.commit()
    except Exception:
        # Try creating the table and retry once
        try:
            _ensure_audit_table(db)
            payload = json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":"))
            db.execute(
                text(
                    """
                    INSERT INTO audit_logs (
                        company_id, user_id, action, entity_type, entity_id, meta, ip_address, created_at
                    ) VALUES (
                        :company_id, :user_id, :action, :entity_type, :entity_id, :meta, :ip, datetime('now')
                    )
                    """
                ),
                {
                    "company_id": company_id,
                    "user_id": user_id,
                    "action": action,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "meta": payload,
                    "ip": ip,
                },
            )
            db.commit()
        except Exception:
            # Last resort: swallow
            try:
                db.rollback()
            except Exception:
                pass


def audit_export(
    db: Session,
    *,
    company_id: Optional[int],
    user_id: Optional[int],
    export_type: str,
    table_or_view: str,
    row_count: int,
    ip: Optional[str],
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Convenience wrapper used by reports/export and similar.
    Persists an 'EXPORT' action with a structured meta payload.
    """
    meta = {
        "export_type": export_type,
        "table_or_view": table_or_view,
        "row_count": row_count,
    }
    if extras:
        meta.update(extras)

    audit_log(
        db,
        company_id=company_id,
        user_id=user_id,
        action="EXPORT",
        entity_type="export",
        entity_id=None,
        meta=meta,
        ip=ip,
    ) 