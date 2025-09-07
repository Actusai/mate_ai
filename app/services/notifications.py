# app/services/notifications.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import json

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.services.audit import audit_log


# ---------------------------------
# Helpers
# ---------------------------------

def _recent_notification_exists(
    db: Session,
    *,
    task_id: int,
    user_id: Optional[int],
    within_days: int = 1,
) -> bool:
    """
    Back-compat helper: returns True if a recent notification exists for (task_id, user_id).
    """
    row = db.execute(
        text(
            """
            SELECT 1
            FROM notifications
            WHERE task_id = :tid
              AND (
                    (:uid IS NULL AND user_id IS NULL)
                 OR (user_id = :uid)
                  )
              AND created_at >= datetime('now', :since)
            LIMIT 1
            """
        ),
        {"tid": task_id, "uid": user_id, "since": f"-{within_days} day"},
    ).fetchone()
    return bool(row)


def _recent_same_payload_exists(
    db: Session,
    *,
    notif_type: str,
    company_id: int,
    ai_system_id: int,
    payload_key: str,
    payload_value: str | int,
    within_hours: int = 6,
) -> bool:
    """
    Generic duplicate guard: checks if a notification with the same type and payload key/value
    was created recently. Uses LIKE on JSON payload for portability (SQLite).
    Assumes JSON is encoded with separators=(',', ':').
    """
    if isinstance(payload_value, int):
        pattern = f'%"{payload_key}":{payload_value}%'
    else:
        safe = str(payload_value).replace('"', '\\"')
        pattern = f'%"{payload_key}":"{safe}"%'

    row = db.execute(
        text(
            """
            SELECT 1
            FROM notifications
            WHERE type = :type
              AND company_id = :cid
              AND ai_system_id = :aid
              AND payload LIKE :pattern
              AND created_at >= datetime('now', :since)
            LIMIT 1
            """
        ),
        {
            "type": notif_type,
            "cid": company_id,
            "aid": ai_system_id,
            "pattern": pattern,
            "since": f"-{within_hours} hour",
        },
    ).fetchone()
    return bool(row)


def _system_name(db: Session, ai_system_id: int) -> Optional[str]:
    row = db.execute(
        text("SELECT name FROM ai_systems WHERE id = :aid LIMIT 1"),
        {"aid": ai_system_id},
    ).fetchone()
    return row[0] if row else None


# ---------------------------------
# Queue phase – task due reminders
# ---------------------------------
def generate_due_task_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    within_days_duplicate_guard: int = 1,
) -> int:
    """
    Based on compliance_tasks:
      - Take tasks not in ('done','cancelled') that have due_date + reminder_days_before.
      - If (due_date - today) <= reminder_days_before (or overdue), enqueue a 'task_due_soon' notification.
      - Skip duplicates for the same (task, user) within 'within_days_duplicate_guard' days.

    Returns the number of created 'queued' notifications.
    """
    filters: List[str] = []
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("t.company_id = :cid")
        params["cid"] = for_company_id

    where = " AND ".join(
        [
            "(t.status IS NULL OR LOWER(t.status) NOT IN ('done','cancelled'))",
            "t.due_date IS NOT NULL",
            "t.reminder_days_before IS NOT NULL",
        ] + filters
    )

    sql = f"""
        SELECT
            t.id            AS task_id,
            t.company_id    AS company_id,
            t.ai_system_id  AS ai_system_id,
            t.owner_user_id AS owner_user_id,
            t.title         AS title,
            t.status        AS status,
            t.due_date      AS due_date,
            t.reminder_days_before AS rem_days,
            s.name          AS system_name
        FROM compliance_tasks t
        LEFT JOIN ai_systems s ON s.id = t.ai_system_id
        WHERE {where}
    """
    rows = db.execute(text(sql), params).mappings().all()

    created = 0
    today = date.today()

    for r in rows:
        due_raw = r["due_date"]
        if not due_raw:
            continue

        # SQLite can return str; coerce to date
        due_date = date.fromisoformat(str(due_raw)[:10])
        rem = int(r["rem_days"] or 0)

        # Reminder if due in <= rem days (or already overdue)
        days_to_due = (due_date - today).days
        if days_to_due > rem:
            continue  # too early

        # Duplicate guard (last N days)
        if _recent_notification_exists(
            db,
            task_id=int(r["task_id"]),
            user_id=int(r["owner_user_id"]) if r["owner_user_id"] is not None else None,
            within_days=within_days_duplicate_guard,
        ):
            continue

        payload = {
            "ai_system_id": r["ai_system_id"],
            "ai_system_name": r["system_name"],
            "task_id": r["task_id"],
            "title": r["title"],
            "status": r["status"],
            "due_date": due_date.isoformat(),
            "reason": "due_soon_or_overdue",
        }

        db.execute(
            text(
                """
                INSERT INTO notifications(
                    company_id, user_id, ai_system_id, task_id,
                    type, channel, payload,
                    status, error, scheduled_at, sent_at, created_at
                ) VALUES (
                    :company_id, :user_id, :ai_system_id, :task_id,
                    'task_due_soon', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": r["company_id"],
                "user_id": r["owner_user_id"],
                "ai_system_id": r["ai_system_id"],
                "task_id": r["task_id"],
                "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        )
        created += 1

    db.commit()
    return created


# ---------------------------------
# New producers – incidents
# ---------------------------------
def produce_incident_created(
    db: Session,
    *,
    incident_id: int,
    company_id: int,
    ai_system_id: int,
    reported_by: Optional[int],
    severity: Optional[str],
    incident_type: Optional[str],
    summary: str,
    occurred_at: Optional[datetime],
    status_val: str,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Enqueue a notification for a newly created incident.
    Returns True if enqueued, False if skipped due to duplicate guard.
    """
    if _recent_same_payload_exists(
        db,
        notif_type="incident_created",
        company_id=company_id,
        ai_system_id=ai_system_id,
        payload_key="incident_id",
        payload_value=incident_id,
        within_hours=duplicate_guard_hours,
    ):
        return False

    system_name = _system_name(db, ai_system_id)
    payload = {
        "title": "New incident reported",
        "incident_id": incident_id,
        "ai_system_id": ai_system_id,
        "ai_system_name": system_name,
        "reported_by": reported_by,
        "severity": severity,
        "type": incident_type,
        "summary": summary,
        "occurred_at": occurred_at.isoformat() if occurred_at else None,
        "status": status_val,
        "reason": "incident_created",
    }

    db.execute(
        text(
            """
            INSERT INTO notifications(
                company_id, user_id, ai_system_id, task_id,
                type, channel, payload,
                status, error, scheduled_at, sent_at, created_at
            ) VALUES (
                :company_id, NULL, :ai_system_id, NULL,
                'incident_created', 'email', :payload,
                'queued', NULL, NULL, NULL, datetime('now')
            )
            """
        ),
        {
            "company_id": company_id,
            "ai_system_id": ai_system_id,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    )
    db.commit()
    return True


def produce_incident_status_changed(
    db: Session,
    *,
    incident_id: int,
    company_id: int,
    ai_system_id: int,
    old_status: str,
    new_status: str,
    severity: Optional[str] = None,
    incident_type: Optional[str] = None,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Enqueue a notification when an incident's status changes.
    Returns True if enqueued, False if skipped due to duplicate guard.
    """
    if _recent_same_payload_exists(
        db,
        notif_type="incident_status_changed",
        company_id=company_id,
        ai_system_id=ai_system_id,
        payload_key="incident_id",
        payload_value=incident_id,
        within_hours=duplicate_guard_hours,
    ):
        return False

    system_name = _system_name(db, ai_system_id)
    payload = {
        "title": "Incident status changed",
        "incident_id": incident_id,
        "ai_system_id": ai_system_id,
        "ai_system_name": system_name,
        "old_status": old_status,
        "new_status": new_status,
        "severity": severity,
        "type": incident_type,
        "reason": "incident_status_changed",
    }

    db.execute(
        text(
            """
            INSERT INTO notifications(
                company_id, user_id, ai_system_id, task_id,
                type, channel, payload,
                status, error, scheduled_at, sent_at, created_at
            ) VALUES (
                :company_id, NULL, :ai_system_id, NULL,
                'incident_status_changed', 'email', :payload,
                'queued', NULL, NULL, NULL, datetime('now')
            )
            """
        ),
        {
            "company_id": company_id,
            "ai_system_id": ai_system_id,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    )
    db.commit()
    return True


# ---------------------------------
# New producer – assessment approved
# ---------------------------------
def produce_assessment_approved(
    db: Session,
    *,
    assessment_id: int,
    ai_system_id: int,
    company_id: int,
    approved_by: int,
    note: Optional[str] = None,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Enqueue a notification when an assessment is approved by the AR / SuperAdmin.
    Returns True if enqueued, False if skipped due to duplicate guard.
    """
    if _recent_same_payload_exists(
        db,
        notif_type="assessment_approved",
        company_id=company_id,
        ai_system_id=ai_system_id,
        payload_key="assessment_id",
        payload_value=assessment_id,
        within_hours=duplicate_guard_hours,
    ):
        return False

    system_name = _system_name(db, ai_system_id)
    payload = {
        "title": "Assessment approved",
        "assessment_id": assessment_id,
        "ai_system_id": ai_system_id,
        "ai_system_name": system_name,
        "approved_by": approved_by,
        "note": note,
        "reason": "assessment_approved",
    }

    db.execute(
        text(
            """
            INSERT INTO notifications(
                company_id, user_id, ai_system_id, task_id,
                type, channel, payload,
                status, error, scheduled_at, sent_at, created_at
            ) VALUES (
                :company_id, NULL, :ai_system_id, NULL,
                'assessment_approved', 'email', :payload,
                'queued', NULL, NULL, NULL, datetime('now')
            )
            """
        ),
        {
            "company_id": company_id,
            "ai_system_id": ai_system_id,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    )
    db.commit()
    return True


# ---------------------------------
# Send phase – mark queued as sent
# ---------------------------------
def send_pending_notifications(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    max_batch: int = 200,
) -> int:
    """
    Marks queued notifications as sent (transport-agnostic placeholder).
    """
    filters = ["status = 'queued'"]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = for_company_id

    where = " AND ".join(filters)
    rows = db.execute(
        text(
            f"""
            SELECT id, company_id, user_id, type, payload
            FROM notifications
            WHERE {where}
            ORDER BY id ASC
            LIMIT :lim
            """
        ),
        {**params, "lim": max_batch},
    ).mappings().all()

    if not rows:
        return 0

    ids = [int(r["id"]) for r in rows]

    db.execute(
        text(
            f"""
            UPDATE notifications
               SET status = 'sent',
                   sent_at = datetime('now')
             WHERE id IN ({",".join(str(i) for i in ids)})
            """
        )
    )
    db.commit()

    # Best-effort audit
    try:
        for r in rows:
            audit_log(
                db,
                company_id=int(r["company_id"]),
                user_id=(int(r["user_id"]) if r["user_id"] is not None else None),
                action="NOTIFICATION_SENT",
                entity_type="notification",
                entity_id=int(r["id"]),
                meta={
                    "type": r["type"],
                    "payload": r["payload"],
                },
                ip=None,
            )
        db.commit()
    except Exception:
        db.rollback()

    return len(ids)


# ---------------------------------
# Full cycle
# ---------------------------------
def run_notifications_cycle(
    db: Session,
    *,
    company_id: Optional[int] = None,
) -> dict:
    """
    Runs both queue and send phases. Returns counters.
    """
    created = generate_due_task_reminders(db, for_company_id=company_id)
    sent = send_pending_notifications(db, for_company_id=company_id)
    return {"created": created, "sent": sent}