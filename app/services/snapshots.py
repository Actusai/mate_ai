# app/services/snapshots.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text


def _to_day_str(d: Optional[date | str]) -> str:
    if d is None:
        return datetime.utcnow().strftime("%Y-%m-%d")
    if isinstance(d, str):
        return d[:10]
    return d.strftime("%Y-%m-%d")


def run_task_stats_daily(
    db: Session,
    *,
    snapshot_day: Optional[date | str] = None,
    company_id: Optional[int] = None,
) -> int:
    """
    Snapshot zadataka po AI sustavu za zadani dan (UTC).
    - overdue_cnt: due_date < kraj dana (snapshot_day 23:59:59) i status != 'done'
    - due_next_7_cnt: [kraj dana, kraj dana + 7d)
    """
    day = _to_day_str(snapshot_day)
    sql = text(
        """
        INSERT INTO task_stats_daily (
            day, company_id, ai_system_id,
            man_total, man_done,
            open_cnt, in_progress_cnt, blocked_cnt, postponed_cnt, done_cnt,
            overdue_cnt, due_next_7_cnt,
            created_at
        )
        SELECT
            :day AS day,
            s.company_id,
            s.id AS ai_system_id,
            SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
            SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done,
            SUM(CASE WHEN t.status='open' THEN 1 ELSE 0 END) AS open_cnt,
            SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) AS in_progress_cnt,
            SUM(CASE WHEN t.status='blocked' THEN 1 ELSE 0 END) AS blocked_cnt,
            SUM(CASE WHEN t.status='postponed' THEN 1 ELSE 0 END) AS postponed_cnt,
            SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS done_cnt,
            SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < datetime(:day, '+1 day') THEN 1 ELSE 0 END) AS overdue_cnt,
            SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date >= datetime(:day, '+1 day') AND t.due_date < datetime(:day, '+8 day') THEN 1 ELSE 0 END) AS due_next_7_cnt,
            datetime('now') AS created_at
        FROM ai_systems s
        LEFT JOIN compliance_tasks t ON t.ai_system_id = s.id
        WHERE (:cid IS NULL OR s.company_id = :cid)
        GROUP BY s.company_id, s.id
        ON CONFLICT(day, ai_system_id) DO UPDATE SET
            man_total        = excluded.man_total,
            man_done         = excluded.man_done,
            open_cnt         = excluded.open_cnt,
            in_progress_cnt  = excluded.in_progress_cnt,
            blocked_cnt      = excluded.blocked_cnt,
            postponed_cnt    = excluded.postponed_cnt,
            done_cnt         = excluded.done_cnt,
            overdue_cnt      = excluded.overdue_cnt,
            due_next_7_cnt   = excluded.due_next_7_cnt,
            created_at       = excluded.created_at
    """
    )
    res = db.execute(sql, {"day": day, "cid": company_id}).rowcount
    return int(res or 0)


def run_owner_task_stats_daily(
    db: Session,
    *,
    snapshot_day: Optional[date | str] = None,
    company_id: Optional[int] = None,
) -> int:
    """
    Snapshot zadataka po vlasniku (owner_user_id) za zadani dan (UTC).
    Bilježi samo redove gdje owner_user_id NIJE NULL.
    """
    day = _to_day_str(snapshot_day)
    sql = text(
        """
        INSERT INTO owner_task_stats_daily (
            day, company_id, owner_user_id, total_cnt, overdue_cnt, created_at
        )
        SELECT
            :day AS day,
            s.company_id,
            t.owner_user_id,
            COUNT(*) AS total_cnt,
            SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < datetime(:day, '+1 day') THEN 1 ELSE 0 END) AS overdue_cnt,
            datetime('now') AS created_at
        FROM compliance_tasks t
        JOIN ai_systems s ON s.id = t.ai_system_id
        WHERE t.owner_user_id IS NOT NULL
          AND (:cid IS NULL OR s.company_id = :cid)
        GROUP BY s.company_id, t.owner_user_id
        ON CONFLICT(day, company_id, owner_user_id) DO UPDATE SET
            total_cnt  = excluded.total_cnt,
            overdue_cnt= excluded.overdue_cnt,
            created_at = excluded.created_at
    """
    )
    res = db.execute(sql, {"day": day, "cid": company_id}).rowcount
    return int(res or 0)


def run_snapshots(
    db: Session,
    *,
    snapshot_day: Optional[date | str] = None,
    company_id: Optional[int] = None,
) -> Dict[str, Any]:
    day = _to_day_str(snapshot_day)
    a = run_task_stats_daily(db, snapshot_day=day, company_id=company_id)
    b = run_owner_task_stats_daily(db, snapshot_day=day, company_id=company_id)
    return {"day": day, "task_stats_rows": a, "owner_stats_rows": b}
