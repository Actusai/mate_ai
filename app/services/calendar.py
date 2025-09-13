# app/services/calendar.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import os, hashlib

from sqlalchemy.orm import Session
from sqlalchemy import text

# RBAC helpers (za provjeru dodijeljenosti)
from app.core.scoping import is_staff_admin, is_super, is_assigned_admin
from app.models.user import User


# ---- Time helpers ------------------------------------------------------------
def _parse_dt(s: Optional[str], *, default: Optional[datetime] = None) -> datetime:
    if s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
    return default or datetime.utcnow()


def _default_window() -> Tuple[datetime, datetime]:
    now = datetime.utcnow()
    return (now - timedelta(days=7), now + timedelta(days=180))


# ---- Validation helpers ------------------------------------------------------
def _validate_visibility(value: str) -> str:
    v = (value or "company").strip().lower()
    if v not in {"company", "mate_internal"}:
        raise ValueError("Invalid visibility (must be 'company' or 'mate_internal').")
    return v


# ---- ICS token helpers (read-only sharing) -----------------------------------
def _secret() -> str:
    return os.getenv("APP_SECRET") or os.getenv("SECRET_KEY") or ""


def make_ics_token(company_id: int) -> str:
    """
    Deterministic token: first 32 hex chars of sha256(APP_SECRET:company_id).
    """
    sec = _secret()
    if not sec:
        return ""  # disabled if no secret configured
    raw = f"{sec}:{int(company_id)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def verify_ics_token(token: Optional[str], company_id: int) -> bool:
    sec = _secret()
    if not sec:
        return False
    return token == make_ics_token(company_id)


# ---- Column exists helper ----------------------------------------------------
def _column_exists(db: Session, table: str, col: str) -> bool:
    try:
        cols = db.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return any((str(c.get("name")) == col) for c in cols)
    except Exception:
        return False


# ---- Event collector ---------------------------------------------------------
def collect_company_events(
    db: Session,
    company_id: int,
    *,
    dt_from: Optional[datetime] = None,
    dt_to: Optional[datetime] = None,
    limit: int = 1000,
    viewer: Optional[User] = None,
    include_internal_for_viewer: bool = False,
) -> List[Dict[str, Any]]:
    """
    Returns normalized events for a company:
      - compliance_tasks due (open)
      - regulatory_deadlines (open)
      - documents with review_due_at
      - (optional) compliance_due_dates rows
      - (optional) calendar_pins (manual milestones)

    visibility rules for calendar_pins:
      - 'company'       → svi s pristupom kompaniji vide
      - 'mate_internal' → vide samo Super/Staff Admini koji su DODIJELJENI toj kompaniji
    """
    if dt_from is None or dt_to is None:
        f, t = _default_window()
    else:
        f, t = dt_from, dt_to

    events: List[Dict[str, Any]] = []

    # -- Tasks (open, due in window)
    try:
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    t.id              AS id,
                    t.ai_system_id    AS ai_system_id,
                    t.title           AS title,
                    t.due_date        AS due_date,
                    t.status          AS status
                FROM compliance_tasks t
                WHERE t.company_id = :cid
                  AND t.due_date IS NOT NULL
                  AND t.due_date >= :from_dt
                  AND t.due_date <= :to_dt
                  AND (t.status IS NULL OR LOWER(t.status) NOT IN ('done','cancelled'))
                ORDER BY t.due_date ASC, t.id ASC
                LIMIT :lim
            """
                ),
                {
                    "cid": company_id,
                    "from_dt": f.isoformat(),
                    "to_dt": t.isoformat(),
                    "lim": limit,
                },
            )
            .mappings()
            .all()
        )
        for r in rows:
            events.append(
                {
                    "source": "task",
                    "id": int(r["id"]),
                    "title": r["title"] or "Task due",
                    "start_at": str(r["due_date"]),
                    "end_at": None,
                    "status": r.get("status"),
                    "ai_system_id": r.get("ai_system_id"),
                    "severity": None,
                }
            )
    except Exception:
        pass

    # -- Regulatory deadlines (open)
    try:
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    d.id            AS id,
                    d.ai_system_id  AS ai_system_id,
                    d.title         AS title,
                    d.due_date      AS due_date,
                    d.status        AS status,
                    d.severity      AS severity
                FROM regulatory_deadlines d
                WHERE d.company_id = :cid
                  AND d.due_date IS NOT NULL
                  AND d.due_date >= :from_dt
                  AND d.due_date <= :to_dt
                  AND (d.status IS NULL OR LOWER(d.status) NOT IN ('done','waived','archived'))
                ORDER BY d.due_date ASC, d.id ASC
                LIMIT :lim
            """
                ),
                {
                    "cid": company_id,
                    "from_dt": f.isoformat(),
                    "to_dt": t.isoformat(),
                    "lim": limit,
                },
            )
            .mappings()
            .all()
        )
        for r in rows:
            events.append(
                {
                    "source": "deadline",
                    "id": int(r["id"]),
                    "title": r["title"] or "Regulatory deadline",
                    "start_at": str(r["due_date"]),
                    "end_at": None,
                    "status": r.get("status"),
                    "ai_system_id": r.get("ai_system_id"),
                    "severity": r.get("severity"),
                }
            )
    except Exception:
        pass

    # -- Documents review_due_at
    try:
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    d.id            AS id,
                    d.ai_system_id  AS ai_system_id,
                    d.name          AS name,
                    d.type          AS type,
                    d.review_due_at AS review_due_at,
                    d.status        AS status
                FROM documents d
                WHERE d.company_id = :cid
                  AND d.review_due_at IS NOT NULL
                  AND d.review_due_at >= :from_dt
                  AND d.review_due_at <= :to_dt
                ORDER BY d.review_due_at ASC, d.id ASC
                LIMIT :lim
            """
                ),
                {
                    "cid": company_id,
                    "from_dt": f.isoformat(),
                    "to_dt": t.isoformat(),
                    "lim": limit,
                },
            )
            .mappings()
            .all()
        )
        for r in rows:
            label = r["name"] or (r.get("type") or "Document")
            events.append(
                {
                    "source": "doc_review",
                    "id": int(r["id"]),
                    "title": f"Document review due: {label}",
                    "start_at": str(r["review_due_at"]),
                    "end_at": None,
                    "status": r.get("status"),
                    "ai_system_id": r.get("ai_system_id"),
                    "severity": None,
                }
            )
    except Exception:
        pass

    # -- Compliance due dates (optional table)
    try:
        rows = (
            db.execute(
                text(
                    """
                SELECT id, ai_system_id, title, scope, due_date, status
                FROM compliance_due_dates
                WHERE company_id = :cid
                  AND due_date IS NOT NULL
                  AND due_date >= :from_dt
                  AND due_date <= :to_dt
                  AND (status IS NULL OR LOWER(status) NOT IN ('done','waived','archived'))
                ORDER BY due_date ASC, id ASC
                LIMIT :lim
            """
                ),
                {
                    "cid": company_id,
                    "from_dt": f.isoformat(),
                    "to_dt": t.isoformat(),
                    "lim": limit,
                },
            )
            .mappings()
            .all()
        )
        for r in rows:
            events.append(
                {
                    "source": "compliance_due",
                    "id": int(r["id"]),
                    "title": r["title"]
                    or f"Compliance due ({r.get('scope') or 'company'})",
                    "start_at": str(r["due_date"]),
                    "end_at": None,
                    "status": r.get("status"),
                    "ai_system_id": r.get("ai_system_id"),
                    "severity": "high",
                }
            )
    except Exception:
        pass

    # -- Manual pins (optional; with visibility)
    try:
        has_visibility = _column_exists(db, "calendar_pins", "visibility")
        base_sql = """
            SELECT id, title, start_at, end_at, severity
            {visibility_col}
            FROM calendar_pins
            WHERE company_id = :cid
              AND start_at IS NOT NULL
              AND start_at >= :from_dt
              AND start_at <= :to_dt
        """
        visibility_col = ", visibility" if has_visibility else ""
        sql = (
            base_sql.format(visibility_col=visibility_col)
            + " ORDER BY start_at ASC, id ASC LIMIT :lim"
        )
        rows = (
            db.execute(
                text(sql),
                {
                    "cid": company_id,
                    "from_dt": f.isoformat(),
                    "to_dt": t.isoformat(),
                    "lim": limit,
                },
            )
            .mappings()
            .all()
        )

        # Decide whether viewer smije vidjeti 'mate_internal'
        allow_internal = False
        if include_internal_for_viewer and viewer is not None:
            # Staff Admin dodijeljen ili SuperAdmin dodijeljen toj kompaniji
            try:
                allow_internal = (
                    is_staff_admin(viewer) and is_assigned_admin(db, viewer, company_id)
                ) or (is_super(viewer) and is_assigned_admin(db, viewer, company_id))
            except Exception:
                allow_internal = False

        for r in rows:
            vis = (r.get("visibility") or "company") if has_visibility else "company"
            if vis == "mate_internal" and not allow_internal:
                continue  # sakrij od klijenta i ne-dodijeljenih

            events.append(
                {
                    "source": "pin",
                    "id": int(r["id"]),
                    "title": r["title"] or "Milestone",
                    "start_at": str(r["start_at"]),
                    "end_at": str(r["end_at"]) if r.get("end_at") else None,
                    "status": None,
                    "ai_system_id": None,
                    "severity": r.get("severity"),
                }
            )
    except Exception:
        pass

    # sort by start_at
    events.sort(key=lambda ev: (ev.get("start_at") or ""))
    return events[:limit]


# ---- ICS builder -------------------------------------------------------------
def _dt_to_ics(s: str) -> str:
    # expects ISO string or datetime string; returns UTC-like stamp YYYYMMDDTHHMMSSZ
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            d = datetime.fromisoformat(s + "T00:00:00+00:00")
        except Exception:
            d = datetime.utcnow()
    return d.strftime("%Y%m%dT%H%M%SZ")


def build_company_calendar_ics(
    company_name: str,
    events: List[Dict[str, Any]],
    *,
    prodid: str = "-//Mate AI//Calendar 1.0//EN",
) -> str:
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{company_name} – Compliance Calendar",
    ]
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for ev in events:
        uid = f"{ev['source']}-{ev['id']}@mateai"
        start = _dt_to_ics(str(ev.get("start_at") or ""))
        end = _dt_to_ics(str(ev.get("end_at") or ev.get("start_at") or ""))
        summary = ev.get("title") or f"{ev['source'].capitalize()}"

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now}",
                f"DTSTART:{start}",
                f"DTEND:{end}",
                f"SUMMARY:{summary}",
                f"CATEGORY:{ev.get('source')}",
                f"DESCRIPTION:status={ev.get('status') or '-'}; ai_system_id={ev.get('ai_system_id') or '-'}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
