# app/worker/scheduler.py
from __future__ import annotations

import os

try:
    from tzlocal import get_localzone  # optional dependency
except Exception:
    get_localzone = None  # type: ignore

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.services.notifications import (
    generate_due_task_reminders,
    generate_stale_evidence_reminders,
    generate_regulatory_deadline_reminders,
    generate_compliance_due_reminders,
    generate_assessment_version_notifications,
    generate_incident_recent_notifications,
    send_pending_notifications,
)


def _with_db(fn, **kwargs) -> int:
    """Run a function with a fresh DB session and return an int result (0 on failure)."""
    db = SessionLocal()
    try:
        return int(fn(db, **kwargs) or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def run_daily_notifications() -> dict:
    """
    One-shot daily pipeline:
      - task due reminders
      - stale evidence (documents review_due_at)
      - regulatory deadlines (T-90/T-30/T-7/overdue)
      - company/system compliance_due_date
      - new assessment versions (last scan window)
      - recent incidents (last scan window)
      - send all queued notifications (mark as sent + audit)
    Scope can be limited with NOTIFY_COMPANY_ID. Scan window via NOTIFY_SCAN_HOURS.
    """
    company_id_env = os.getenv("NOTIFY_COMPANY_ID")
    company_id = (
        int(company_id_env) if (company_id_env and company_id_env.isdigit()) else None
    )
    scan_hours = int(os.getenv("NOTIFY_SCAN_HOURS", "24"))

    common = {}
    if company_id is not None:
        common["for_company_id"] = company_id

    created_tasks = _with_db(generate_due_task_reminders, **common)
    created_docs = _with_db(generate_stale_evidence_reminders, **common)
    created_reg = _with_db(generate_regulatory_deadline_reminders, **common)
    created_comp = _with_db(generate_compliance_due_reminders, **common)
    created_subs = _with_db(generate_subscription_expiring_reminders)
    created_ass = _with_db(
        generate_assessment_version_notifications,
        within_hours_window=scan_hours,
        **common,
    )
    created_inc = _with_db(
        generate_incident_recent_notifications,
        within_hours_window=scan_hours,
        **common,
    )
    sent = _with_db(
        send_pending_notifications,
        **({"for_company_id": company_id} if company_id is not None else {}),
    )

    return {
        "created_task_due": created_tasks,
        "created_stale_evidence": created_docs,
        "created_reg_deadlines": created_reg,
        "created_compliance_due": created_comp,
        "created_assessment_versions": created_ass,
        "created_incidents": created_inc,
        "sent": sent,
    }


def make_scheduler() -> BackgroundScheduler:
    """
    Create and return a BackgroundScheduler instance configured from env:
      - APP_TIMEZONE           (default: system tz via tzlocal or 'UTC')
      - APP_SCHEDULER_HOUR     (default: 6)
      - APP_SCHEDULER_MINUTE   (default: 0)
    """
    # Resolve timezone
    if get_localzone:
        try:
            tzname = os.getenv("APP_TIMEZONE") or str(get_localzone())
        except Exception:
            tzname = "UTC"
    else:
        tzname = os.getenv("APP_TIMEZONE", "UTC")

    hour = int(os.getenv("APP_SCHEDULER_HOUR", "6"))  # default 06:00 local time
    minute = int(os.getenv("APP_SCHEDULER_MINUTE", "0"))

    sched = BackgroundScheduler(timezone=tzname)

    # Daily job
    sched.add_job(
        run_daily_notifications,
        CronTrigger(hour=hour, minute=minute),
        id="daily_notifications",
        replace_existing=True,
    )

    return sched
