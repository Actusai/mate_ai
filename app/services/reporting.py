# app/services/reporting.py
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

# -----------------------------
# Time helpers
# -----------------------------
def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _in_days_iso(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------
# Compliance helpers
# -----------------------------
def compliance_status_from_counts(man_total: int, man_done: int, overdue_cnt: int) -> str:
    """
    Derive compliance_status from mandatory-task coverage and overdue count.
    Rules:
      - Any overdue mandatory tasks -> 'non_compliant'
      - 100% mandatory done         -> 'compliant'
      - >= 80% mandatory done       -> 'at_risk'
      - otherwise                   -> 'non_compliant'
    """
    man_total = int(man_total or 0)
    man_done = int(man_done or 0)
    overdue_cnt = int(overdue_cnt or 0)

    if overdue_cnt > 0:
        return "non_compliant"
    if man_total == 0:
        return "compliant"
    pct = 100.0 * man_done / max(man_total, 1)
    if pct >= 100.0:
        return "compliant"
    if pct >= 80.0:
        return "at_risk"
    return "non_compliant"

def compliance_status_from_pct(pct: float, overdue_cnt: int) -> str:
    """
    Variant when we only have compliance_pct and overdue_cnt.
    """
    overdue_cnt = int(overdue_cnt or 0)
    pct = float(pct or 0.0)
    if overdue_cnt > 0:
        return "non_compliant"
    if pct >= 100.0:
        return "compliant"
    if pct >= 80.0:
        return "at_risk"
    return "non_compliant"

def compute_effective_risk(risk_tier: Optional[str], compliance_status: str) -> str:
    """
    Map (risk_tier, compliance_status) -> effective_risk badge.
      - High-risk & non_compliant     -> 'critical'
      - High-risk & at_risk           -> 'high'
      - Moderate-risk & non_compliant -> 'high'
      - Moderate-risk & at_risk       -> 'medium'
      - Minimal/unknown:
          non_compliant -> 'medium'
          at_risk       -> 'low'
          compliant     -> 'low'
    """
    rt = (risk_tier or "unknown").lower()
    cs = (compliance_status or "at_risk").lower()

    if rt == "high_risk":
        if cs == "non_compliant":
            return "critical"
        if cs == "at_risk":
            return "high"
        return "medium"  # compliant but still high-risk domain

    if rt in {"moderate_risk", "limited_risk"}:
        if cs == "non_compliant":
            return "high"
        if cs == "at_risk":
            return "medium"
        return "low"

    # minimal_risk / unknown
    if cs == "non_compliant":
        return "medium"
    if cs == "at_risk":
        return "low"
    return "low"


# -----------------------------
# Derived compliance for a single system (USED by other routes)
# -----------------------------
def compute_compliance_snapshot(db: Session, ai_system_id: int) -> Dict[str, int]:
    """
    Compact snapshot used for status decision and audit meta:
      - mandatory_total
      - mandatory_done
      - overdue_cnt  (not-done & past due)
      - due_7_cnt    (not-done & due in next 7 days)
    """
    now = _now_iso()
    in7 = _in_days_iso(7)

    row = db.execute(
        text("""
            SELECT
              SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
              SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done,
              SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < :now THEN 1 ELSE 0 END) AS overdue_cnt,
              SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date >= :now AND t.due_date < :in7 THEN 1 ELSE 0 END) AS due_7_cnt
            FROM compliance_tasks t
            WHERE t.ai_system_id = :aid
        """),
        {"aid": ai_system_id, "now": now, "in7": in7},
    ).mappings().first() or {}

    return {
        "mandatory_total": int(row.get("man_total") or 0),
        "mandatory_done": int(row.get("man_done") or 0),
        "overdue_cnt": int(row.get("overdue_cnt") or 0),
        "due_7_cnt": int(row.get("due_7_cnt") or 0),
    }

def compute_compliance_status_for_system(db: Session, ai_system_id: int) -> str:
    """
    Computes 'compliant' | 'at_risk' | 'non_compliant' for a given AI system
    using the counts of mandatory tasks and overdue count.
    """
    snap = compute_compliance_snapshot(db, ai_system_id)
    return compliance_status_from_counts(
        snap["mandatory_total"], snap["mandatory_done"], snap["overdue_cnt"]
    )


# -----------------------------
# COMPANY DASHBOARD METRICS
# -----------------------------
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
    per_system_pct = [100.0 if (r["man_total"] or 0) == 0 else (100.0 * (r["man_done"] or 0) / (r["man_total"] or 1)) for r in comp_rows]
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


def systems_table(db: Session, company_id: int) -> List[Dict[str, Any]]:
    """
    Returns systems summary with compliance_pct / overdue_cnt and computed badges.
    """
    now = _now_iso()
    in7 = _in_days_iso(7)
    rows = db.execute(text("""
        WITH t AS (
            SELECT s.id AS system_id, s.name AS system_name,
                   COALESCE(s.risk_tier, 'unknown') AS risk_tier,
                   SUM(CASE WHEN c.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
                   SUM(CASE WHEN c.mandatory=1 AND c.status='done' THEN 1 ELSE 0 END) AS man_done,
                   SUM(CASE WHEN c.status='open' THEN 1 ELSE 0 END) AS open_cnt,
                   SUM(CASE WHEN c.status!='done' AND c.due_date IS NOT NULL AND c.due_date < :now THEN 1 ELSE 0 END) AS overdue_cnt,
                   SUM(CASE WHEN c.status!='done' AND c.due_date IS NOT NULL AND c.due_date >= :now AND c.due_date < :in7 THEN 1 ELSE 0 END) AS due_7_cnt
            FROM ai_systems s
            LEFT JOIN compliance_tasks c ON c.ai_system_id = s.id
            WHERE s.company_id = :cid
            GROUP BY s.id, s.name, s.risk_tier
        )
        SELECT system_id, system_name, risk_tier, man_total, man_done, overdue_cnt, due_7_cnt,
               CASE WHEN man_total=0 THEN 100.0 ELSE ROUND(100.0 * man_done * 1.0 / man_total, 2) END AS compliance_pct,
               open_cnt
        FROM t
        ORDER BY risk_tier DESC, compliance_pct ASC
    """), {"cid": company_id, "now": now, "in7": in7}).mappings().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        cs = compliance_status_from_counts(d.get("man_total"), d.get("man_done"), d.get("overdue_cnt"))
        er = compute_effective_risk(d.get("risk_tier"), cs)
        d["compliance_status"] = cs
        d["effective_risk"] = er
        out.append(d)
    return out


def overdue_by_owner(db: Session, company_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT u.id AS user_id, u.full_name AS name, u.email AS email, COUNT(*) AS overdue_cnt
        FROM compliance_tasks t
        LEFT JOIN users u ON u.id = t.owner_user_id
        WHERE t.company_id = :cid AND t.status != 'done'
              AND t.due_date IS NOT NULL AND t.due_date < datetime('now')
        GROUP BY u.id, u.full_name, u.email
        ORDER BY overdue_cnt DESC, name ASC
        LIMIT :lim
    """), {"cid": company_id, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def upcoming_deadlines(db: Session, company_id: int, in_days: int = 14) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT t.id, t.title, t.due_date, t.owner_user_id,
               s.id AS system_id, s.name AS system_name
        FROM compliance_tasks t
        JOIN ai_systems s ON s.id = t.ai_system_id
        WHERE t.company_id = :cid AND t.status != 'done'
              AND t.due_date IS NOT NULL AND t.due_date >= datetime('now')
              AND t.due_date < :inN
        ORDER BY t.due_date ASC
        LIMIT 200
    """), {"cid": company_id, "inN": _in_days_iso(in_days)}).mappings().all()
    return [dict(r) for r in rows]


def team_overview(db: Session, company_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        WITH task_counts AS (
            SELECT owner_user_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status!='done' AND due_date IS NOT NULL AND due_date < datetime('now') THEN 1 ELSE 0 END) AS overdue
            FROM compliance_tasks
            WHERE company_id = :cid
            GROUP BY owner_user_id
        )
        SELECT u.id AS user_id, u.full_name AS name, u.email AS email,
               u.role AS role, u.invite_status AS invite_status,
               COALESCE(tc.total, 0) AS tasks_total,
               COALESCE(tc.overdue, 0) AS tasks_overdue
        FROM users u
        LEFT JOIN task_counts tc ON tc.owner_user_id = u.id
        WHERE u.company_id = :cid
        ORDER BY tasks_overdue DESC, name ASC
    """), {"cid": company_id}).mappings().all()
    return [dict(r) for r in rows]


def reference_breakdown(db: Session, company_id: int) -> List[Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT t.reference, COUNT(*) AS total,
               SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS done_cnt,
               SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < datetime('now') THEN 1 ELSE 0 END) AS overdue_cnt
        FROM compliance_tasks t
        JOIN ai_systems s ON s.id = t.ai_system_id
        WHERE s.company_id = :cid AND t.reference IS NOT NULL AND t.reference <> ''
        GROUP BY t.reference
        ORDER BY overdue_cnt DESC, total DESC
    """), {"cid": company_id}).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------
# SUPERADMIN OVERVIEW
# -----------------------------
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


# -----------------------------
# EXPORT HELPERS (for /reports/export)
# -----------------------------
def export_ai_systems(db: Session, company_id: int, member_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return AI systems for a company; if member_user_id is set, filter by membership."""
    if member_user_id:
        query = text("""
            SELECT s.*
            FROM ai_systems s
            JOIN ai_system_members m ON m.ai_system_id = s.id
            WHERE s.company_id = :cid AND m.user_id = :uid
        """)
        params = {"cid": company_id, "uid": member_user_id}
    else:
        query = text("SELECT * FROM ai_systems WHERE company_id = :cid")
        params = {"cid": company_id}

    rows = db.execute(query, params).mappings().all()
    return [dict(r) for r in rows]

def export_compliance_tasks(db: Session, company_id: int, member_user_id: Optional[int] = None,
                            ai_system_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return tasks for a company; optionally filter by member and/or specific AI system."""
    base = ["t.company_id = :cid"]
    params: Dict[str, Any] = {"cid": company_id}
    joins = []

    if ai_system_id is not None:
        base.append("t.ai_system_id = :aid")
        params["aid"] = ai_system_id

    if member_user_id is not None:
        joins.append("JOIN ai_system_members m ON m.ai_system_id = t.ai_system_id")
        base.append("m.user_id = :uid")
        params["uid"] = member_user_id

    where_clause = " AND ".join(base)
    join_clause = " ".join(joins)

    query = text(f"""
        SELECT t.*
        FROM compliance_tasks t
        {join_clause}
        WHERE {where_clause}
    """)

    rows = db.execute(query, params).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------
# Alerts
# -----------------------------
def company_alerts(db: Session, company_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Returns list of systems that are high-risk AND non-compliant.
    (Useful to surface on the company dashboard.)
    """
    now = _now_iso()
    rows = db.execute(text("""
        WITH agg AS (
            SELECT s.id AS system_id, s.name AS system_name,
                   COALESCE(s.risk_tier, 'unknown') AS risk_tier,
                   SUM(CASE WHEN t.mandatory=1 THEN 1 ELSE 0 END) AS man_total,
                   SUM(CASE WHEN t.mandatory=1 AND t.status='done' THEN 1 ELSE 0 END) AS man_done,
                   SUM(CASE WHEN t.status!='done' AND t.due_date IS NOT NULL AND t.due_date < :now THEN 1 ELSE 0 END) AS overdue_cnt
            FROM ai_systems s
            LEFT JOIN compliance_tasks t ON t.ai_system_id = s.id
            WHERE s.company_id = :cid
            GROUP BY s.id, s.name, s.risk_tier
        )
        SELECT system_id, system_name, risk_tier, man_total, man_done, overdue_cnt,
               CASE WHEN man_total=0 THEN 100.0 ELSE ROUND(100.0 * man_done * 1.0 / man_total, 2) END AS compliance_pct
        FROM agg
        ORDER BY compliance_pct ASC, overdue_cnt DESC, system_name ASC
        LIMIT :lim
    """), {"cid": company_id, "now": now, "lim": limit}).mappings().all()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        cs = compliance_status_from_counts(d.get("man_total"), d.get("man_done"), d.get("overdue_cnt"))
        if (d.get("risk_tier") or "").lower() == "high_risk" and cs == "non_compliant":
            d["compliance_status"] = cs
            d["effective_risk"] = compute_effective_risk(d.get("risk_tier"), cs)
            out.append(d)
    # Already ordered by lower pct and higher overdue
    return out