# app/services/reporting.py
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _in_days_iso(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

def compute_company_kpis(db: Session, company_id: int, window_days: int = 30) -> Dict[str, Any]:
    now = _now_iso()
    window_start = (datetime.utcnow() - timedelta(days=window_days)).strftime("%Y-%m-%d %H:%M:%S")

    systems_row = db.execute(text("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN lifecycle_stage='production' THEN 1 ELSE 0 END) AS production_cnt,
               SUM(CASE WHEN lifecycle_stage='development' THEN 1 ELSE 0 END) AS development_cnt,
               SUM(CASE WHEN lifecycle_stage='archived' THEN 1 ELSE 0 END) AS archived_cnt
        FROM ai_systems WHERE company_id = :cid
    """), {"cid": company_id}).mappings().first() or {}

    risk_rows = db.execute(text("""
        SELECT COALESCE(risk_tier, 'unknown') AS risk_tier, COUNT(*) AS cnt
        FROM ai_systems WHERE company_id = :cid
        GROUP BY COALESCE(risk_tier, 'unknown')
    """), {"cid": company_id}).mappings().all()
    risk_distribution = {r["risk_tier"]: r["cnt"] for r in risk_rows}

    comp_rows = db.execute(text("""
        SELECT s.id AS system_id,
               SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
               SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done
        FROM ai_systems s
        LEFT JOIN compliance_tasks t ON t.ai_system_id = s.id
        WHERE s.company_id = :cid
        GROUP BY s.id
    """), {"cid": company_id}).mappings().all()
    per_system_pct = [100.0 if r["man_total"] == 0 else (100.0 * r["man_done"] / r["man_total"]) for r in comp_rows]
    avg_compliance_pct = round(sum(per_system_pct) / len(per_system_pct), 2) if per_system_pct else 100.0

    status_row = db.execute(text("""
        SELECT SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_cnt,
               SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress_cnt,
               SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked_cnt,
               SUM(CASE WHEN status='postponed' THEN 1 ELSE 0 END) AS postponed_cnt,
               SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_cnt
        FROM compliance_tasks WHERE company_id = :cid
    """), {"cid": company_id}).mappings().first() or {}

    status_window_row = db.execute(text("""
        SELECT SUM(CASE WHEN status='open' AND updated_at >= :wstart THEN 1 ELSE 0 END) AS open_w,
               SUM(CASE WHEN status='in_progress' AND updated_at >= :wstart THEN 1 ELSE 0 END) AS in_progress_w,
               SUM(CASE WHEN status='blocked' AND updated_at >= :wstart THEN 1 ELSE 0 END) AS blocked_w,
               SUM(CASE WHEN status='postponed' AND updated_at >= :wstart THEN 1 ELSE 0 END) AS postponed_w,
               SUM(CASE WHEN status='done' AND updated_at >= :wstart THEN 1 ELSE 0 END) AS done_w
        FROM compliance_tasks WHERE company_id = :cid
    """), {"cid": company_id, "wstart": window_start}).mappings().first() or {}

    overdue_cnt = db.execute(text("""
        SELECT COUNT(*) FROM compliance_tasks
        WHERE company_id = :cid AND status != 'done'
              AND due_date IS NOT NULL AND due_date < :now
    """), {"cid": company_id, "now": now}).scalar() or 0

    due_next_7_cnt = db.execute(text("""
        SELECT COUNT(*) FROM compliance_tasks
        WHERE company_id = :cid AND status != 'done'
              AND due_date IS NOT NULL AND due_date >= :now AND due_date < :in7
    """), {"cid": company_id, "now": now, "in7": _in_days_iso(7)}).scalar() or 0

    return {
        "systems": {"total": systems_row.get("total", 0),
                     "production": systems_row.get("production_cnt", 0),
                     "development": systems_row.get("development_cnt", 0),
                     "archived": systems_row.get("archived_cnt", 0),
                     "risk_distribution": risk_distribution},
        "compliance": {"avg_compliance_pct": avg_compliance_pct},
        "tasks": {
            "status_counts": {
                "open": status_row.get("open_cnt", 0),
                "in_progress": status_row.get("in_progress_cnt", 0),
                "blocked": status_row.get("blocked_cnt", 0),
                "postponed": status_row.get("postponed_cnt", 0),
                "done": status_row.get("done_cnt", 0)},
            "window_counts": {
                "open": status_window_row.get("open_w", 0),
                "in_progress": status_window_row.get("in_progress_w", 0),
                "blocked": status_window_row.get("blocked_w", 0),
                "postponed": status_window_row.get("postponed_w", 0),
                "done": status_window_row.get("done_w", 0)},
            "overdue": overdue_cnt,
            "due_next_7": due_next_7_cnt}}

# ... tvoje funkcije: systems_table, overdue_by_owner, upcoming_deadlines, team_overview, reference_breakdown ...

# Dodana funkcija compute_superadmin_overview ispod 👇

def compute_superadmin_overview(db: Session) -> Dict[str, Any]:
    kpi_row = db.execute(text("""
        WITH sys_cnt AS (
            SELECT COUNT(*) AS systems_total FROM ai_systems
        ),
        comp AS (
            SELECT c.id AS company_id,
                   AVG(CASE WHEN z.man_total=0 THEN 100.0 ELSE 100.0 * z.man_done * 1.0 / z.man_total END) AS avg_pct
            FROM companies c
            LEFT JOIN (
                SELECT s.id as system_id, s.company_id,
                       SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
                       SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done
                FROM ai_systems s
                LEFT JOIN compliance_tasks t ON t.ai_system_id = s.id
                GROUP BY s.id, s.company_id
            ) z ON z.company_id = c.id
            GROUP BY c.id
        ),
        overdue AS (
            SELECT company_id, COUNT(*) AS overdue_cnt
            FROM compliance_tasks
            WHERE status != 'done' AND due_date IS NOT NULL AND due_date < datetime('now')
            GROUP BY company_id
        )
        SELECT (SELECT COUNT(*) FROM companies) AS companies_total,
               (SELECT systems_total FROM sys_cnt) AS systems_total,
               ROUND(AVG(comp.avg_pct), 2) AS avg_compliance_pct,
               COALESCE(SUM(overdue.overdue_cnt), 0) AS overdue_total
        FROM comp
        LEFT JOIN overdue ON 1=1
    """)).mappings().first() or {}

    companies = db.execute(text("""
        WITH pkg AS (
            SELECT cp.company_id, p.name AS package_name
            FROM company_packages cp
            JOIN packages p ON p.id = cp.package_id
            GROUP BY cp.company_id
        ),
        comp AS (
            SELECT c.id AS company_id,
                   AVG(CASE WHEN z.man_total=0 THEN 100.0 ELSE 100.0 * z.man_done * 1.0 / z.man_total END) AS avg_pct,
                   SUM(z.overdue_cnt) AS overdue_cnt
            FROM companies c
            LEFT JOIN (
                SELECT s.id as system_id, s.company_id,
                       SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
                       SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done,
                       SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < datetime('now') THEN 1 ELSE 0 END) AS overdue_cnt
                FROM ai_systems s
                LEFT JOIN compliance_tasks t ON t.ai_system_id = s.id
                GROUP BY s.id, s.company_id
            ) z ON z.company_id = c.id
            GROUP BY c.id
        ),
        sys AS (
            SELECT company_id, COUNT(*) AS systems_cnt FROM ai_systems GROUP BY company_id
        ),
        usr AS (
            SELECT company_id, COUNT(*) AS users_cnt FROM users GROUP BY company_id
        )
        SELECT c.id AS company_id, c.name AS company_name, c.status, c.last_activity_at,
               COALESCE(pkg.package_name, '-') AS package_name,
               COALESCE(sys.systems_cnt, 0) AS systems_cnt,
               COALESCE(usr.users_cnt, 0) AS users_cnt,
               ROUND(COALESCE(comp.avg_pct, 100), 2) AS avg_compliance_pct,
               COALESCE(comp.overdue_cnt, 0) AS overdue_cnt
        FROM companies c
        LEFT JOIN pkg ON pkg.company_id = c.id
        LEFT JOIN comp ON comp.company_id = c.id
        LEFT JOIN sys ON sys.company_id = c.id
        LEFT JOIN usr ON usr.company_id = c.id
        ORDER BY avg_compliance_pct ASC, overdue_cnt DESC, company_name ASC
    """)).mappings().all()

    return {
        "kpi": {
            "companies_total": kpi_row.get("companies_total", 0),
            "systems_total": kpi_row.get("systems_total", 0),
            "avg_compliance_pct": kpi_row.get("avg_compliance_pct", 100.0),
            "overdue_total": kpi_row.get("overdue_total", 0)
        },
        "companies": [dict(r) for r in companies]
    }
