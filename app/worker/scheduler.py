# app/worker/scheduler.py
from __future__ import annotations
import os
from tzlocal import get_localzone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.services.notifications import (
    generate_due_task_reminders,
    generate_stale_evidence_reminders,
    generate_deadline_reminders,
    send_pending_notifications,
)

def _with_db(fn, **kwargs) -> int:
    db = SessionLocal()
    try:
        return int(fn(db, **kwargs) or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()

def run_daily_notifications() -> dict:
    created_tasks   = _with_db(generate_due_task_reminders)
    created_docs    = _with_db(generate_stale_evidence_reminders)
    created_deadlns = _with_db(generate_deadline_reminders)
    sent            = _with_db(send_pending_notifications)
    return {
        "created_task_due": created_tasks,
        "created_stale_evidence": created_docs,
        "created_deadline": created_deadlns,
        "sent": sent,
    }

def make_scheduler() -> BackgroundScheduler:
    # Timezone & schedule time (env overridable)
    try:
        tzname = os.getenv("APP_TIMEZONE") or str(get_localzone())
    except Exception:
        tzname = "UTC"

    hour = int(os.getenv("APP_SCHEDULER_HOUR", "6"))     # default 06:00 local time
    minute = int(os.getenv("APP_SCHEDULER_MINUTE", "0"))

    sched = BackgroundScheduler(timezone=tzname)
    sched.add_job(
        run_daily_notifications,
        CronTrigger(hour=hour, minute=minute),
        id="daily_notifications",
        replace_existing=True,
    )
    return sched