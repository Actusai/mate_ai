# app/services/notifications.py
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, Dict, Any, List
import json

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.services.audit import audit_log
from app.services.compliance import (
    get_fria_status,
    fria_required_for_system,
)  # FRIA support


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
    ai_system_id: Optional[int],
    payload_key: str,
    payload_value: str | int,
    within_hours: int = 6,
) -> bool:
    """
    Duplicate guard: checks if a notification with the same type and payload key/value
    was created recently. Uses LIKE on JSON payload for portability (SQLite).
    Also matches rows with NULL ai_system_id when ai_system_id is None.
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
               AND (
                     (:aid IS NULL AND ai_system_id IS NULL)
                  OR (ai_system_id = :aid)
                   )
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


def _column_exists(db: Session, table: str, column: str) -> bool:
    """
    SQLite-safe check. No bound params for PRAGMA; only used on hardcoded table names.
    """
    try:
        rows = db.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        for r in rows:
            if str(r.get("name")) == column:
                return True
    except Exception:
        return False
    return False


def _stage_from_days(
    days_to_due: int, thresholds: tuple[int, int, int]
) -> Optional[str]:
    """
    Return a stage tag ('T-90'/'T-30'/'T-7'/'overdue') or None if not at a checkpoint.
    """
    t90, t30, t7 = thresholds
    if days_to_due < 0:
        return "overdue"
    if days_to_due == t7:
        return f"T-{t7}"
    if days_to_due == t30:
        return f"T-{t30}"
    if days_to_due == t90:
        return f"T-{t90}"
    return None


# ---------------------------------
# Message templates (subject/body) – EN only
# ---------------------------------
def render_message(notif_type: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Lightweight templates for transport/audit. Keep EN user-facing text.
    """

    def _fmt_dt(v: Any) -> str:
        try:
            return str(v)
        except Exception:
            return ""

    if notif_type == "task_due_soon":
        title = payload.get("title") or "Task"
        due = payload.get("due_date")
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[Action needed] '{title}' is due soon"
        body = (
            f"Task '{title}' for system '{sys_name}' is due on {due}.\n"
            "Please review and complete it to stay compliant."
        )
        return {"subject": subject, "body": body}

    if notif_type == "assessment_approved":
        aid = payload.get("assessment_id")
        subject = f"[Assessment] Version {aid} approved"
        body = (
            f"Assessment version {aid} has been approved.\n"
            f"Approver user ID: {payload.get('approver_user_id')}.\n"
            f"Note: {payload.get('note') or '-'}"
        )
        return {"subject": subject, "body": body}

    if notif_type == "assessment_version_created":
        aid = payload.get("assessment_id")
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[Assessment] New version created for '{sys_name}'"
        body = (
            f"A new assessment version (ID: {aid}) was created for system '{sys_name}'."
        )
        return {"subject": subject, "body": body}

    if notif_type == "incident_created":
        iid = payload.get("incident_id")
        subject = f"[Incident] New incident opened (ID: {iid})"
        body = (
            f"An incident was created (ID: {iid}). Severity: {payload.get('severity') or '-'}; "
            f"Type: {payload.get('type') or '-'}; Status: {payload.get('status') or '-'}.\n"
            f"Summary: {payload.get('summary') or '-'}."
        )
        return {"subject": subject, "body": body}

    if notif_type == "incident_status_changed":
        iid = payload.get("incident_id")
        subject = f"[Incident] Status changed for incident {iid}"
        body = (
            f"Incident {iid} status changed: {payload.get('old_status')} → {payload.get('new_status')}.\n"
            f"Severity: {payload.get('severity') or '-'}; Type: {payload.get('type') or '-'}."
        )
        return {"subject": subject, "body": body}

    if notif_type == "stale_evidence":
        subject = "[Evidence] Review overdue"
        body = (
            f"Evidence '{payload.get('document_name')}' ({payload.get('document_type')}) "
            f"is overdue for review since {_fmt_dt(payload.get('review_due_at'))}."
        )
        return {"subject": subject, "body": body}

    if notif_type == "regulatory_deadline":
        subject = "[Regulatory] Upcoming deadline"
        body = (
            f"'{payload.get('title')}' is approaching at stage {payload.get('stage')} "
            f"(due: {_fmt_dt(payload.get('due_date'))})."
        )
        return {"subject": subject, "body": body}

    if notif_type == "compliance_due":
        scope = payload.get("scope") or "company"
        subject = f"[Compliance] {scope.capitalize()} compliance due reminder"
        body = (
            f"Compliance due date ({payload.get('stage')}) is approaching "
            f"on {_fmt_dt(payload.get('due_date'))}."
        )
        return {"subject": subject, "body": body}

    # FRIA nudges
    if notif_type == "fria_required":
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[FRIA] Action required for '{sys_name}'"
        body = (
            f"The AI system '{sys_name}' is classified as high-risk and requires a Fundamental Rights Impact Assessment (FRIA).\n"
            "No completed FRIA was found. Please initiate and upload FRIA documentation."
        )
        return {"subject": subject, "body": body}

    if notif_type == "fria_in_progress":
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[FRIA] Reminder for '{sys_name}'"
        body = (
            f"FRIA for the AI system '{sys_name}' is in progress. "
            "Please ensure it is completed and the final document is uploaded."
        )
        return {"subject": subject, "body": body}

    # AR assignment lifecycle
    if notif_type == "ar_assigned":
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[AR] Authorized Representative assigned to '{sys_name}'"
        body = (
            f"An Authorized Representative (user ID: {payload.get('ar_user_id')}) "
            f"was assigned to AI system '{sys_name}'."
        )
        return {"subject": subject, "body": body}

    if notif_type == "ar_unassigned":
        sys_name = payload.get("ai_system_name") or ""
        subject = f"[AR] Authorized Representative unassigned from '{sys_name}'"
        body = (
            f"The Authorized Representative was unassigned from AI system '{sys_name}'. "
            f"Change initiated by user ID: {payload.get('unset_by_user_id')}."
        )
        return {"subject": subject, "body": body}

    if notif_type == "subscription_expiring":
        subject = "[Billing] Subscription expiring soon"
        company = payload.get("company_name") or "Company"
        pkg = payload.get("package_name") or "subscription"
        ends = _fmt_dt(payload.get("ends_at"))
        stage = payload.get("stage") or ""  # npr. T-30 / T-7 / T-1 / overdue

        body = (
            f"Subscription for '{company}' ({pkg}) is expiring on {ends}"
            f"{f' ({stage})' if stage else ''}.\n"
            "Please review the renewal to avoid service interruption."
        )

        return {"subject": subject, "body": body}

    if notif_type == "subscription_expiring":
        plan = (
            payload.get("package_name") or payload.get("package_id") or "Subscription"
        )
        company = payload.get("company_name") or ""
        ends = payload.get("ends_at")
        stage = payload.get("stage") or ""
        subject = f"[Billing] Subscription expiring soon ({stage or 'upcoming'})"
        body = (
            f"Subscription '{plan}' for company '{company}' is approaching its end date ({ends}).\n"
            f"Stage: {stage or 'T-?'}.\n"
            "Please review renewal options to avoid service interruption."
        )
        return {"subject": subject, "body": body}

    # Fallback
    return {
        "subject": f"[{notif_type}] Notification",
        "body": json.dumps(payload, ensure_ascii=False),
    }


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
        ]
        + filters
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
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


# ---------------------------------
# Event-style producers – incidents
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
# Producers – assessment approved & AR assignment
# ---------------------------------
def produce_assessment_approved(
    db: Session,
    *,
    company_id: int,
    ai_system_id: int,
    assessment_id: int,
    approver_user_id: int,
    note: Optional[str] = None,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Enqueue 'assessment_approved' notification with a duplicate guard keyed on assessment_id.
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

    payload = {
        "assessment_id": assessment_id,
        "ai_system_id": ai_system_id,
        "approver_user_id": approver_user_id,
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
                :company_id, :user_id, :ai_system_id, NULL,
                'assessment_approved', 'email', :payload,
                'queued', NULL, NULL, NULL, datetime('now')
            )
            """
        ),
        {
            "company_id": company_id,
            "user_id": approver_user_id,
            "ai_system_id": ai_system_id,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    )
    db.commit()
    return True


def produce_ar_assigned(
    db: Session,
    *,
    company_id: int,
    ai_system_id: int,
    ar_user_id: int,
    set_by_user_id: Optional[int] = None,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Notify that an Authorized Representative was assigned to a system.
    """
    marker = f"ar_assign:{ai_system_id}:{ar_user_id}"
    if _recent_same_payload_exists(
        db,
        notif_type="ar_assigned",
        company_id=company_id,
        ai_system_id=ai_system_id,
        payload_key="marker",
        payload_value=marker,
        within_hours=duplicate_guard_hours,
    ):
        return False

    system_name = _system_name(db, ai_system_id)
    payload = {
        "marker": marker,
        "ai_system_id": ai_system_id,
        "ai_system_name": system_name,
        "ar_user_id": ar_user_id,
        "set_by_user_id": set_by_user_id,
        "reason": "ar_assigned",
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
                'ar_assigned', 'email', :payload,
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


def produce_ar_unassigned(
    db: Session,
    *,
    company_id: int,
    ai_system_id: int,
    unset_by_user_id: Optional[int] = None,
    duplicate_guard_hours: int = 6,
) -> bool:
    """
    Notify that an Authorized Representative was unassigned from a system.
    """
    marker = f"ar_unassign:{ai_system_id}"
    if _recent_same_payload_exists(
        db,
        notif_type="ar_unassigned",
        company_id=company_id,
        ai_system_id=ai_system_id,
        payload_key="marker",
        payload_value=marker,
        within_hours=duplicate_guard_hours,
    ):
        return False

    system_name = _system_name(db, ai_system_id)
    payload = {
        "marker": marker,
        "ai_system_id": ai_system_id,
        "ai_system_name": system_name,
        "unset_by_user_id": unset_by_user_id,
        "reason": "ar_unassigned",
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
                'ar_unassigned', 'email', :payload,
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
# NEW – scan producers (no events required)
# ---------------------------------
def generate_assessment_version_notifications(
    db: Session,
    *,
    within_hours_window: int = 24,
    for_company_id: Optional[int] = None,
) -> int:
    """
    Scan ai_assessments created within the last window and emit 'assessment_version_created'
    notifications if not already present.
    Uses a minimal set of columns to avoid schema coupling.
    """
    filters = [f"a.created_at >= datetime('now', :since)"]
    params: Dict[str, Any] = {"since": f"-{within_hours_window} hour"}

    if for_company_id is not None:
        filters.append("a.company_id = :cid")
        params["cid"] = for_company_id

    where = " AND ".join(filters)
    sql = f"""
        SELECT
            a.id           AS assessment_id,
            a.company_id   AS company_id,
            a.ai_system_id AS ai_system_id,
            a.created_at   AS created_at,
            s.name         AS system_name
        FROM ai_assessments a
        LEFT JOIN ai_systems s ON s.id = a.ai_system_id
        WHERE {where}
        ORDER BY a.id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()
    if not rows:
        return 0

    created = 0
    for r in rows:
        if _recent_same_payload_exists(
            db,
            notif_type="assessment_version_created",
            company_id=int(r["company_id"]),
            ai_system_id=int(r["ai_system_id"]),
            payload_key="assessment_id",
            payload_value=int(r["assessment_id"]),
            within_hours=within_hours_window,
        ):
            continue

        payload = {
            "assessment_id": int(r["assessment_id"]),
            "ai_system_id": int(r["ai_system_id"]),
            "ai_system_name": r["system_name"],
            "reason": "assessment_version_created",
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
                    'assessment_version_created', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(r["company_id"]),
                "ai_system_id": int(r["ai_system_id"]),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


def generate_incident_recent_notifications(
    db: Session,
    *,
    within_hours_window: int = 24,
    for_company_id: Optional[int] = None,
) -> int:
    """
    Scan incidents created in the last window and enqueue 'incident_created' notifications
    (safe for idempotency via duplicate guard).
    """
    # Tolerate missing table
    try:
        db.execute(text("SELECT 1 FROM incidents LIMIT 1"))
    except Exception:
        return 0

    filters = ["i.created_at >= datetime('now', :since)"]
    params: Dict[str, Any] = {"since": f"-{within_hours_window} hour"}

    if for_company_id is not None:
        filters.append("i.company_id = :cid")
        params["cid"] = for_company_id

    sql = f"""
        SELECT
            i.id          AS incident_id,
            i.company_id  AS company_id,
            i.ai_system_id AS ai_system_id,
            i.reported_by AS reported_by,
            i.severity    AS severity,
            i.type        AS type,
            i.summary     AS summary,
            i.occurred_at AS occurred_at,
            i.status      AS status
        FROM incidents i
        WHERE {" AND ".join(filters)}
        ORDER BY i.id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()
    if not rows:
        return 0

    created = 0
    for r in rows:
        # reuse the event-style dedupe logic
        if _recent_same_payload_exists(
            db,
            notif_type="incident_created",
            company_id=int(r["company_id"]),
            ai_system_id=(
                int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
            ),
            payload_key="incident_id",
            payload_value=int(r["incident_id"]),
            within_hours=within_hours_window,
        ):
            continue

        system_name = (
            _system_name(db, int(r["ai_system_id"]))
            if r["ai_system_id"] is not None
            else None
        )
        payload = {
            "incident_id": int(r["incident_id"]),
            "ai_system_id": (
                int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
            ),
            "ai_system_name": system_name,
            "reported_by": r.get("reported_by"),
            "severity": r.get("severity"),
            "type": r.get("type"),
            "summary": r.get("summary"),
            "occurred_at": (
                r.get("occurred_at").isoformat() if r.get("occurred_at") else None
            ),
            "status": r.get("status"),
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
                "company_id": int(r["company_id"]),
                "ai_system_id": (
                    int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
                ),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


# ---------------------------------
# FRIA reminders (high-risk systems without completed FRIA)
# ---------------------------------
def generate_fria_required_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    duplicate_guard_hours: int = 168,  # weekly
) -> int:
    """
    Scan AI systems and enqueue nudges when FRIA is required but not completed:
      - 'fria_required' if missing completely
      - 'fria_in_progress' if a FRIA doc exists but is not complete
    """
    filters = []
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = for_company_id

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = (
        db.execute(
            text(
                f"""
            SELECT id AS ai_system_id, company_id, name, risk_tier
            FROM ai_systems
            {where}
            ORDER BY id ASC
            """
            ),
            params,
        )
        .mappings()
        .all()
    )

    if not rows:
        return 0

    created = 0
    for r in rows:
        # Decide if FRIA is required (Python-side, tolerant to risk_tier variants)
        class _Sys:  # lightweight adapter for fria_required_for_system
            id: int = int(r["ai_system_id"])
            risk_tier: Optional[str] = r.get("risk_tier")

        sys_like = _Sys()

        if not fria_required_for_system(sys_like):
            continue

        status = get_fria_status(db, int(r["ai_system_id"])) or {}
        st = (status.get("status") or "unknown").lower()

        if st == "completed":
            continue  # nothing to do

        notif_type = (
            "fria_required" if st in {"missing", "unknown"} else "fria_in_progress"
        )

        # Duplicate guard keyed by system id
        if _recent_same_payload_exists(
            db,
            notif_type=notif_type,
            company_id=int(r["company_id"]),
            ai_system_id=int(r["ai_system_id"]),
            payload_key="ai_system_id",
            payload_value=int(r["ai_system_id"]),
            within_hours=duplicate_guard_hours,
        ):
            continue

        payload = {
            "ai_system_id": int(r["ai_system_id"]),
            "ai_system_name": r["name"],
            "status": st,
            "reason": notif_type,
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
                    :type, 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(r["company_id"]),
                "ai_system_id": int(r["ai_system_id"]),
                "type": notif_type,
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


# ---------------------------------
# Regulatory deadlines & compliance_due_date reminders
# ---------------------------------
def generate_regulatory_deadline_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    thresholds: tuple[int, int, int] = (90, 30, 7),
    duplicate_guard_hours: int = 24,
) -> int:
    """
    Enqueue reminders for rows in regulatory_deadlines at T-90/T-30/T-7 and when overdue.
    Skips 'done'/'waived'/'archived'.
    """
    # If the table doesn't exist, just no-op
    try:
        db.execute(text("SELECT 1 FROM regulatory_deadlines LIMIT 1"))
    except Exception:
        return 0

    filters = [
        "(status IS NULL OR LOWER(status) NOT IN ('done','waived','archived'))",
        "due_date IS NOT NULL",
    ]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = for_company_id

    sql = f"""
        SELECT id, company_id, ai_system_id, title, description, due_date, severity, status
        FROM regulatory_deadlines
        WHERE {" AND ".join(filters)}
        ORDER BY due_date ASC, id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()
    if not rows:
        return 0

    created = 0
    today = date.today()

    for r in rows:
        due_raw = r["due_date"]
        if not due_raw:
            continue
        due = date.fromisoformat(str(due_raw)[:10])
        days_to_due = (due - today).days
        stage = _stage_from_days(days_to_due, thresholds)
        if stage is None:
            continue

        marker = f"regdl:{int(r['id'])}:{stage}"

        if _recent_same_payload_exists(
            db,
            notif_type="regulatory_deadline",
            company_id=int(r["company_id"]),
            ai_system_id=(
                int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
            ),
            payload_key="deadline_marker",
            payload_value=marker,
            within_hours=duplicate_guard_hours,
        ):
            continue

        payload = {
            "reason": "regulatory_deadline",
            "deadline_id": int(r["id"]),
            "deadline_marker": marker,
            "title": r["title"],
            "description": r["description"],
            "due_date": due.isoformat(),
            "stage": stage,
            "severity": r["severity"],
            "status": r["status"],
            "ai_system_id": r["ai_system_id"],
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
                    'regulatory_deadline', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(r["company_id"]),
                "ai_system_id": (
                    int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
                ),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


def _generate_compliance_due_from_table(
    db: Session,
    *,
    for_company_id: Optional[int],
    thresholds: tuple[int, int, int],
    duplicate_guard_hours: int,
) -> int:
    """
    New path: compliance_due_dates table (if present).
    """
    try:
        db.execute(text("SELECT 1 FROM compliance_due_dates LIMIT 1"))
    except Exception:
        return 0

    filters = ["due_date IS NOT NULL"]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = for_company_id

    sql = f"""
        SELECT id, company_id, ai_system_id, title, scope, due_date, status
        FROM compliance_due_dates
        WHERE {" AND ".join(filters)} AND (status IS NULL OR LOWER(status) NOT IN ('done','waived','archived'))
        ORDER BY due_date ASC, id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()

    if not rows:
        return 0

    created = 0
    today = date.today()
    for r in rows:
        due_raw = r["due_date"]
        if not due_raw:
            continue
        due = date.fromisoformat(str(due_raw)[:10])
        stage = _stage_from_days((due - today).days, thresholds)
        if stage is None:
            continue

        marker = f"comp_due:table:{int(r['id'])}:{stage}:{due.isoformat()}"
        if _recent_same_payload_exists(
            db,
            notif_type="compliance_due",
            company_id=int(r["company_id"]),
            ai_system_id=(
                int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
            ),
            payload_key="deadline_marker",
            payload_value=marker,
            within_hours=duplicate_guard_hours,
        ):
            continue

        payload = {
            "reason": "compliance_due",
            "scope": r["scope"] or ("ai_system" if r["ai_system_id"] else "company"),
            "company_id": int(r["company_id"]),
            "ai_system_id": (
                int(r["ai_system_id"]) if r["ai_system_id"] is not None else None
            ),
            "title": r["title"],
            "due_date": due.isoformat(),
            "stage": stage,
            "status": r["status"],
            "deadline_marker": marker,
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
                    'compliance_due', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": payload["company_id"],
                "ai_system_id": payload["ai_system_id"],
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


def _generate_compliance_due_from_legacy_columns(
    db: Session,
    *,
    for_company_id: Optional[int],
    thresholds: tuple[int, int, int],
    duplicate_guard_hours: int,
) -> int:
    """
    Legacy path: companies.compliance_due_date and ai_systems.compliance_due_date (if columns exist).
    """
    created = 0
    today = date.today()

    # Company-level
    if _column_exists(db, "companies", "compliance_due_date"):
        filters = ["compliance_due_date IS NOT NULL"]
        params: Dict[str, Any] = {}
        if for_company_id is not None:
            filters.append("id = :cid")
            params["cid"] = for_company_id
        sql = f"""
            SELECT id AS company_id, compliance_due_date AS due_dt
            FROM companies
            WHERE {" AND ".join(filters)}
        """
        rows = db.execute(text(sql), params).mappings().all()
        for r in rows:
            due_raw = r["due_dt"]
            if not due_raw:
                continue
            due = date.fromisoformat(str(due_raw)[:10])
            stage = _stage_from_days((due - today).days, thresholds)
            if stage is None:
                continue

            marker = (
                f"comp_due:company:{int(r['company_id'])}:{stage}:{due.isoformat()}"
            )
            if _recent_same_payload_exists(
                db,
                notif_type="compliance_due",
                company_id=int(r["company_id"]),
                ai_system_id=None,
                payload_key="deadline_marker",
                payload_value=marker,
                within_hours=duplicate_guard_hours,
            ):
                continue

            payload = {
                "reason": "compliance_due",
                "scope": "company",
                "company_id": int(r["company_id"]),
                "due_date": due.isoformat(),
                "stage": stage,
                "deadline_marker": marker,
            }
            db.execute(
                text(
                    """
                    INSERT INTO notifications(
                        company_id, user_id, ai_system_id, task_id,
                        type, channel, payload,
                        status, error, scheduled_at, sent_at, created_at
                    ) VALUES (
                        :company_id, NULL, NULL, NULL,
                        'compliance_due', 'email', :payload,
                        'queued', NULL, NULL, NULL, datetime('now')
                    )
                    """
                ),
                {
                    "company_id": int(r["company_id"]),
                    "payload": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
            created += 1

    # System-level
    if _column_exists(db, "ai_systems", "compliance_due_date"):
        filters = ["compliance_due_date IS NOT NULL"]
        params: Dict[str, Any] = {}
        if for_company_id is not None:
            filters.append("company_id = :cid")
            params["cid"] = for_company_id
        sql = f"""
            SELECT id AS ai_system_id, company_id, compliance_due_date AS due_dt, name
            FROM ai_systems
            WHERE {" AND ".join(filters)}
        """
        rows = db.execute(text(sql), params).mappings().all()
        for r in rows:
            due_raw = r["due_dt"]
            if not due_raw:
                continue
            due = date.fromisoformat(str(due_raw)[:10])
            stage = _stage_from_days((due - today).days, thresholds)
            if stage is None:
                continue

            marker = (
                f"comp_due:system:{int(r['ai_system_id'])}:{stage}:{due.isoformat()}"
            )
            if _recent_same_payload_exists(
                db,
                notif_type="compliance_due",
                company_id=int(r["company_id"]),
                ai_system_id=int(r["ai_system_id"]),
                payload_key="deadline_marker",
                payload_value=marker,
                within_hours=duplicate_guard_hours,
            ):
                continue

            payload = {
                "reason": "compliance_due",
                "scope": "ai_system",
                "company_id": int(r["company_id"]),
                "ai_system_id": int(r["ai_system_id"]),
                "ai_system_name": r["name"],
                "due_date": due.isoformat(),
                "stage": stage,
                "deadline_marker": marker,
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
                        'compliance_due', 'email', :payload,
                        'queued', NULL, NULL, NULL, datetime('now')
                    )
                    """
                ),
                {
                    "company_id": int(r["company_id"]),
                    "ai_system_id": int(r["ai_system_id"]),
                    "payload": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
            created += 1

    db.commit()
    return created


def generate_compliance_due_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    thresholds: tuple[int, int, int] = (90, 30, 7),
    duplicate_guard_hours: int = 24,
) -> int:
    """
    Enqueue reminders for compliance due dates using either:
      - compliance_due_dates table (if present), and/or
      - legacy columns companies.compliance_due_date / ai_systems.compliance_due_date
    """
    created = 0
    created += _generate_compliance_due_from_table(
        db,
        for_company_id=for_company_id,
        thresholds=thresholds,
        duplicate_guard_hours=duplicate_guard_hours,
    )
    created += _generate_compliance_due_from_legacy_columns(
        db,
        for_company_id=for_company_id,
        thresholds=thresholds,
        duplicate_guard_hours=duplicate_guard_hours,
    )
    return created


# ---------------------------------
# Subscription expiring reminders
# ---------------------------------
def generate_subscription_expiring_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    thresholds: tuple[int, int, int] = (30, 7, 1),
    duplicate_guard_hours: int = 24,
) -> int:
    """
    Enqueue 'subscription_expiring' notifications for company_packages nearing expiry.
    Stages: T-30, T-7, T-1 and 'overdue' (after ends_at).
    Tolerant to schema differences (optional status column).
    Dedupe by (company_package_id, stage, ends_at) within duplicate_guard_hours.
    """
    # If the base table doesn't exist, no-op safely
    try:
        db.execute(text("SELECT 1 FROM company_packages LIMIT 1"))
    except Exception:
        return 0

    filters = ["cp.ends_at IS NOT NULL"]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("cp.company_id = :cid")
        params["cid"] = for_company_id

    # Optional: filter out cancelled/archived/expired if 'status' column exists
    def _column_exists_local(table: str, col: str) -> bool:
        try:
            cols = db.execute(text(f"PRAGMA table_info({table})")).mappings().all()
            return any((str(c.get("name")) == col) for c in cols)
        except Exception:
            return False

    has_status = _column_exists_local("company_packages", "status")
    if has_status:
        filters.append("LOWER(cp.status) NOT IN ('cancelled','archived','expired')")

    sql = f"""
        SELECT
            cp.id           AS company_package_id,
            cp.company_id   AS company_id,
            cp.package_id   AS package_id,
            cp.starts_at    AS starts_at,
            cp.ends_at      AS ends_at,
            {"cp.status       AS status," if has_status else "NULL AS status,"}
            c.name          AS company_name,
            COALESCE(p.name, p.code) AS package_name
        FROM company_packages cp
        LEFT JOIN companies c ON c.id = cp.company_id
        LEFT JOIN packages  p ON p.id = cp.package_id
        WHERE {" AND ".join(filters)}
        ORDER BY cp.ends_at ASC, cp.id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()
    if not rows:
        return 0

    created = 0
    today = date.today()

    for r in rows:
        ends_raw = r["ends_at"]
        if not ends_raw:
            continue

        # Normalize to date (support datetime/str)
        try:
            ends_dt = date.fromisoformat(str(ends_raw)[:10])
        except Exception:
            continue

        days_to_end = (ends_dt - today).days
        stage = _stage_from_days(days_to_end, thresholds)
        if stage is None:
            continue  # not at a checkpoint

        # Dedupe marker: package + stage + exact end date
        marker = f"sub:{int(r['company_package_id'])}:{stage}:{ends_dt.isoformat()}"

        if _recent_same_payload_exists(
            db,
            notif_type="subscription_expiring",
            company_id=int(r["company_id"]),
            ai_system_id=None,
            payload_key="subscription_marker",
            payload_value=marker,
            within_hours=duplicate_guard_hours,
        ):
            continue

        payload = {
            "reason": "subscription_expiring",
            "subscription_marker": marker,
            "company_id": int(r["company_id"]),
            "company_package_id": int(r["company_package_id"]),
            "package_id": r.get("package_id"),
            "package_name": r.get("package_name"),
            "company_name": r.get("company_name"),
            "ends_at": ends_dt.isoformat(),
            "stage": stage,  # 'T-30' | 'T-7' | 'T-1' | 'overdue'
            "status": r.get("status"),
        }

        db.execute(
            text(
                """
                INSERT INTO notifications(
                    company_id, user_id, ai_system_id, task_id,
                    type, channel, payload,
                    status, error, scheduled_at, sent_at, created_at
                ) VALUES (
                    :company_id, NULL, NULL, NULL,
                    'subscription_expiring', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(r["company_id"]),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


# --------------------------------
# Subscription reminders
# --------------------------------
def generate_subscription_expiry_reminders(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    thresholds: tuple[int, int, int] = (30, 7, 1),
    duplicate_guard_hours: int = 24,
) -> int:
    """
    Enqueue 'subscription_expiring' for active company_packages whose ends_at is near.
    Dedupe per (company_package_id, stage) within duplicate_guard_hours.
    """
    # Tolerant: ako tablice ne postoje, tiho izađi
    try:
        db.execute(text("SELECT 1 FROM company_packages LIMIT 1"))
        db.execute(text("SELECT 1 FROM packages LIMIT 1"))
        db.execute(text("SELECT 1 FROM companies LIMIT 1"))
    except Exception:
        return 0

    filters = ["cp.status = 'active'", "cp.ends_at IS NOT NULL"]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("cp.company_id = :cid")
        params["cid"] = for_company_id

    sql = f"""
        SELECT
            cp.id           AS cp_id,
            cp.company_id   AS company_id,
            cp.ends_at      AS ends_at,
            p.id            AS package_id,
            p.name          AS package_name,
            c.name          AS company_name
        FROM company_packages cp
        JOIN packages p ON p.id = cp.package_id
        JOIN companies c ON c.id = cp.company_id
        WHERE {' AND '.join(filters)}
        ORDER BY cp.ends_at ASC, cp.id ASC
    """
    rows = db.execute(text(sql), params).mappings().all()
    if not rows:
        return 0

    today = date.today()
    created = 0
    for r in rows:
        ends_raw = r["ends_at"]
        if not ends_raw:
            continue
        ends = date.fromisoformat(str(ends_raw)[:10])
        days_to_end = (ends - today).days
        stage = _stage_from_days(days_to_end, thresholds)
        if stage is None:
            continue  # nismo na kontrolnoj točki

        marker = f"subexp:{int(r['cp_id'])}:{stage}"  # npr. subexp:42:T-7

        if _recent_same_payload_exists(
            db,
            notif_type="subscription_expiring",
            company_id=int(r["company_id"]),
            ai_system_id=None,
            payload_key="expiry_marker",
            payload_value=marker,
            within_hours=duplicate_guard_hours,
        ):
            continue

        payload = {
            "expiry_marker": marker,
            "company_package_id": int(r["cp_id"]),
            "company_id": int(r["company_id"]),
            "company_name": r["company_name"],
            "package_id": int(r["package_id"]),
            "package_name": r["package_name"],
            "ends_at": ends.isoformat(),
            "stage": stage,
        }

        db.execute(
            text(
                """
                INSERT INTO notifications(
                    company_id, user_id, ai_system_id, task_id,
                    type, channel, payload,
                    status, error, scheduled_at, sent_at, created_at
                ) VALUES (
                    :company_id, NULL, NULL, NULL,
                    'subscription_expiring', 'email', :payload,
                    'queued', NULL, NULL, NULL, datetime('now')
                )
                """
            ),
            {
                "company_id": int(r["company_id"]),
                "payload": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        )
        created += 1

    db.commit()
    return created


# ---------------------------------
# Send phase – mark queued as sent (render templates, audit)
# ---------------------------------
def send_pending_notifications(
    db: Session,
    *,
    for_company_id: Optional[int] = None,
    max_batch: int = 200,
) -> int:
    """
    Marks queued notifications as sent (transport-agnostic placeholder).
    Also renders message templates and audits them (subject/body in meta).
    """
    filters = ["status = 'queued'"]
    params: Dict[str, Any] = {}
    if for_company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = for_company_id

    where = " AND ".join(filters)
    rows = (
        db.execute(
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
        )
        .mappings()
        .all()
    )

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

    # Best-effort audit with templated payload
    try:
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except Exception:
                payload = {"raw": r["payload"]}
            templ = render_message(str(r["type"]), payload)
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
                    "subject": templ.get("subject"),
                    "body": templ.get("body"),
                },
                ip=None,
            )
        db.commit()
    except Exception:
        db.rollback()

    return len(ids)


# ---------------------------------
# Cycles
# ---------------------------------
def run_notifications_cycle(
    db: Session,
    *,
    company_id: Optional[int] = None,
    scan_hours: int = 24,
) -> Dict[str, Any]:
    """
    Full cycle: tasks + stale evidence + regulatory deadlines + compliance due dates
    + new assessment versions + newly created incidents (scan) + FRIA nudges
    + subscriptions expiring.
    Returns detailed counters (and total created for back-compat).
    """
    created_task = generate_due_task_reminders(db, for_company_id=company_id)
    created_evid = generate_stale_evidence_reminders(db, for_company_id=company_id)
    created_reg = generate_regulatory_deadline_reminders(db, for_company_id=company_id)
    created_comp = generate_compliance_due_reminders(db, for_company_id=company_id)
    created_ass = generate_assessment_version_notifications(
        db, within_hours_window=scan_hours, for_company_id=company_id
    )
    created_inc = generate_incident_recent_notifications(
        db, within_hours_window=scan_hours, for_company_id=company_id
    )
    created_fria = generate_fria_required_reminders(db, for_company_id=company_id)
    created_subs = generate_subscription_expiring_reminders(
        db, for_company_id=company_id
    )

    sent = send_pending_notifications(db, for_company_id=company_id)

    total_created = (
        created_task
        + created_evid
        + created_reg
        + created_comp
        + created_ass
        + created_inc
        + created_fria
        + created_subs
    )
    return {
        "created_task": created_task,
        "created_stale_evidence": created_evid,
        "created_reg_deadlines": created_reg,
        "created_compliance_due": created_comp,
        "created_assessment_versions": created_ass,
        "created_incidents": created_inc,
        "created_fria": created_fria,
        "created_subscription_expiring": created_subs,
        "created": total_created,  # back-compat aggregate
        "sent": sent,
    }


def run_all_notifications_cycle(
    db: Session,
    *,
    company_id: Optional[int] = None,
    scan_hours: int = 24,
) -> dict:
    """Alias to full cycle (kept for callers that expect run_all_* to exist)."""
    return run_notifications_cycle(db, company_id=company_id, scan_hours=scan_hours)
