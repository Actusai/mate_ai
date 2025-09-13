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
    Order:
      - X-Forwarded-For (first in the list)
      - X-Real-IP
      - request.client.host
    """
    if request is None:
        return None
    try:
        # RFC 7239 'Forwarded' header (rare, but check first if present)
        fwd = request.headers.get("forwarded")
        if fwd:
            # simplest parse: take first "for=" token
            parts = [p.strip() for p in fwd.split(";")]
            for p in parts:
                if p.lower().startswith("for="):
                    val = p.split("=", 1)[1].strip().strip('"')
                    if val:
                        # may contain obfuscated values; still better than nothing
                        return val

        xff = request.headers.get("x-forwarded-for")
        if xff:
            # take the first IP in the chain
            first = xff.split(",")[0].strip()
            if first:
                return first

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        client = getattr(request, "client", None)
        return getattr(client, "host", None)
    except Exception:
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


def _dumps_meta(meta: Optional[Dict[str, Any]]) -> str:
    try:
        return json.dumps(meta or {}, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # last resort: stringify
        try:
            return json.dumps({"raw": str(meta)}, ensure_ascii=False)
        except Exception:
            return "{}"


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
        payload = _dumps_meta(meta)
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
            payload = _dumps_meta(meta)
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


# -----------------------------
# NEW – Convenience wrappers for documents (DoC generator, etc.)
# -----------------------------
def audit_doc_generated(
    db: Session,
    *,
    company_id: Optional[int],
    user_id: Optional[int],
    ai_system_id: Optional[int],
    document_id: Optional[int],
    storage_url: Optional[str],
    format: str = "pdf",
    ip: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Records a DOC_GENERATED audit entry for EU Conformity or similar documents.
    """
    meta: Dict[str, Any] = {
        "format": format,
        "ai_system_id": ai_system_id,
        "storage_url": storage_url,
    }
    if extras:
        meta.update(extras)

    audit_log(
        db,
        company_id=company_id,
        user_id=user_id,
        action="DOC_GENERATED",
        entity_type="document",
        entity_id=document_id,
        meta=meta,
        ip=ip,
    )


def audit_doc_sent(
    db: Session,
    *,
    company_id: Optional[int],
    user_id: Optional[int],
    document_id: Optional[int],
    channel: str = "email",
    target: Optional[str] = None,
    ip: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Records a DOC_SENT audit entry when a document is sent/shared (e.g., to AI Office).
    """
    meta: Dict[str, Any] = {
        "channel": channel,
        "target": target,
    }
    if extras:
        meta.update(extras)

    audit_log(
        db,
        company_id=company_id,
        user_id=user_id,
        action="DOC_SENT",
        entity_type="document",
        entity_id=document_id,
        meta=meta,
        ip=ip,
    )
