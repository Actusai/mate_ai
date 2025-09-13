# app/services/reporting.py
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text

# MODELS (best-effort imports; tolerate if some are missing)
from app.models.company import Company
from app.models.ai_system import AISystem
from app.models.user import User

try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:  # pragma: no cover
    ComplianceTask = None  # type: ignore

try:
    from app.models.incident import Incident  # pragma: no cover
except Exception:  # pragma: no cover
    Incident = None  # type: ignore

try:
    from app.models.regulatory_deadline import RegulatoryDeadline  # pragma: no cover
except Exception:  # pragma: no cover
    RegulatoryDeadline = None  # type: ignore

# Optional: global metrics snapshot (for superadmin overview)
try:
    from app.services.metrics import compute_metrics_summary as _compute_metrics_summary  # type: ignore
except Exception:  # pragma: no cover
    _compute_metrics_summary = None  # type: ignore


# -----------------------------
# Small risk/compliance helpers
# -----------------------------
def compliance_status_from_pct(pct: float, overdue_cnt: int) -> str:
    """
    Compute a coarse compliance badge from completion percentage and overdue count.

    Rules (aligned with services.compliance.compliance_status_from_metrics):
      - If there is ANY overdue item -> 'non_compliant'
      - If completion is effectively 100% -> 'compliant'
      - Otherwise -> 'at_risk'

    Tolerates pct given as 0..1 or 0..100:
      - values > 1.0 are treated as percentages and divided by 100.
    """
    try:
        p = float(pct if pct is not None else 0.0)
    except Exception:
        p = 0.0

    # Normalize scale to 0..1
    if p > 1.0:
        p = p / 100.0

    try:
        ov = int(overdue_cnt or 0)
    except Exception:
        ov = 0

    if ov > 0:
        return "non_compliant"
    if p >= 0.99999:  # effectively 100%
        return "compliant"
    return "at_risk"


def compute_effective_risk(
    risk_tier: Optional[str], compliance_status: Optional[str]
) -> str:
    """
    Map inherent risk + compliance badge to a coarse effective risk level for dashboards.
      Returns one of: 'critical' | 'high' | 'medium' | 'low'.
    """
    rt = (risk_tier or "").lower().replace("-", "_")
    cs = (compliance_status or "").lower()

    if rt in {"high_risk", "high"}:
        # High inherent risk stays high even if compliant; becomes critical if non-compliant.
        return "critical" if cs not in {"compliant", "at_risk"} else "high"

    if rt in {"limited_risk", "limited"}:
        return "high" if cs == "non_compliant" else "medium"

    if rt in {"minimal_risk", "minimal"}:
        if cs == "compliant":
            return "low"
        return "medium"

    if rt in {"prohibited", "prohibited_risk"}:
        return "critical"

    # Fallback if tier unknown
    return "medium"


# -----------------------------
# Local helpers (tolerant DB inspection)
# -----------------------------
def _table_exists(db: Session, table: str) -> bool:
    try:
        db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _column_exists(db: Session, table: str, column: str) -> bool:
    try:
        rows = db.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return any(str(r.get("name")) == column for r in rows)
    except Exception:
        return False


def _month_bounds(yyyy_mm: Optional[str]) -> Tuple[datetime, datetime]:
    if yyyy_mm:
        y, m = yyyy_mm.split("-")
        y, m = int(y), int(m)
        start = datetime(y, m, 1)
    else:
        now = datetime.utcnow()
        start = datetime(now.year, now.month, 1)
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)
    return start, end


# -----------------------------
# Company-scoped finance (tolerant)
# -----------------------------
def _compute_company_finance(
    db: Session, company_id: int, *, month: Optional[str] = None
) -> Dict[str, Any]:
    """
    Company-scoped MRR/ARR/ARPU and logo churn % (current month).
    Works even if some columns are missing (falls back gracefully).
    """
    if not _table_exists(db, "company_packages"):
        return {"mrr": 0.0, "arr": 0.0, "arpu": 0.0, "logo_churn_pct": 0.0}

    has_status = _column_exists(db, "company_packages", "status")
    has_bterm = _column_exists(db, "company_packages", "billing_term")
    has_upm = _column_exists(db, "company_packages", "unit_price_month")
    has_upy = _column_exists(db, "company_packages", "unit_price_year")
    has_starts = _column_exists(db, "company_packages", "starts_at")
    has_ends = _column_exists(db, "company_packages", "ends_at")

    join_pkg = (
        "JOIN packages p ON p.id = cp.package_id"
        if _table_exists(db, "packages")
        else ""
    )
    has_pm = _column_exists(db, "packages", "price_month")
    has_py = _column_exists(db, "packages", "price_year")

    # price expressions
    pm_expr = "p.price_month" if has_pm else "NULL"
    py_expr = "p.price_year" if has_py else "NULL"
    upm_expr = "cp.unit_price_month" if has_upm else "NULL"
    upy_expr = "cp.unit_price_year" if has_upy else "NULL"

    if has_bterm:
        price_case = f"""
            CASE
              WHEN LOWER(cp.billing_term) = 'monthly' THEN COALESCE({upm_expr}, {pm_expr}, 0)
              WHEN LOWER(cp.billing_term) = 'yearly'  THEN COALESCE({upy_expr}, {py_expr}, 0) / 12.0
              ELSE COALESCE({pm_expr}, 0)
            END
        """
    else:
        # If no billing_term, treat as monthly and prefer package monthly price
        price_case = f"COALESCE({upm_expr}, {pm_expr}, 0)"

    now = datetime.utcnow().isoformat(sep=" ")
    active_filter = ["cp.company_id = :cid"]
    if has_starts:
        active_filter.append("cp.starts_at <= :now")
    if has_ends:
        active_filter.append("(cp.ends_at IS NULL OR cp.ends_at > :now)")
    if has_status:
        active_filter.append("LOWER(cp.status) = 'active'")

    # Active subscriptions (for MRR/ARR/ARPU)
    rows = (
        db.execute(
            text(
                f"""
            SELECT {price_case} AS eff_price
            FROM company_packages cp
            {join_pkg}
            WHERE {" AND ".join(active_filter)}
            """
            ),
            {"cid": company_id, "now": now},
        )
        .mappings()
        .all()
    )

    mrr = float(sum(float(r["eff_price"] or 0.0) for r in rows))
    arr = mrr * 12.0
    # For company scope, ARPU = MRR / #active_companies (0 or 1 effectively)
    active_companies = 1 if rows else 0
    arpu = (mrr / active_companies) if active_companies > 0 else 0.0

    # Logo churn% (companies with package ended in month window) — at company scope this is 0% or 100%
    start_m, end_m = _month_bounds(month)
    if has_ends:
        ended = db.execute(
            text(
                "SELECT 1 FROM company_packages cp "
                "WHERE cp.company_id = :cid AND cp.ends_at IS NOT NULL "
                "AND cp.ends_at >= :start AND cp.ends_at < :end LIMIT 1"
            ),
            {
                "cid": company_id,
                "start": start_m.isoformat(sep=" "),
                "end": end_m.isoformat(sep=" "),
            },
        ).fetchone()
        active_start = db.execute(
            text(
                f"SELECT 1 FROM company_packages cp WHERE cp.company_id = :cid "
                f"{'AND cp.starts_at <= :start' if has_starts else ''} "
                f"{'AND (cp.ends_at IS NULL OR cp.ends_at > :start)' if has_ends else ''} "
                f"{'AND LOWER(cp.status) = \'active\'' if has_status else ''} "
                "LIMIT 1"
            ),
            {"cid": company_id, "start": start_m.isoformat(sep=" ")},
        ).fetchone()
        churn = 100.0 if (ended and active_start) else 0.0
    else:
        churn = 0.0

    return {
        "mrr": round(mrr, 2),
        "arr": round(arr, 2),
        "arpu": round(arpu, 2),
        "logo_churn_pct": round(churn, 2),
        "month": start_m.strftime("%Y-%m"),
    }


# -----------------------------
# Core company dashboard helpers
# -----------------------------
def compute_company_kpis(
    db: Session, company_id: int, *, window_days: int = 30
) -> Dict[str, Any]:
    """
    Very lightweight KPIs: systems count, open tasks, overdue tasks, (optional) open incidents.
    Now also returns 'finance' block (company-scoped MRR/ARR/ARPU/churn%).
    """
    now = datetime.utcnow()
    since = now - timedelta(days=window_days)

    systems_cnt = db.query(AISystem).filter(AISystem.company_id == company_id).count()

    open_tasks = 0
    overdue_tasks = 0
    if ComplianceTask is not None:
        q = db.query(ComplianceTask).filter(ComplianceTask.company_id == company_id)
        open_tasks = q.filter(
            ~func.lower(ComplianceTask.status).in_(("done", "cancelled"))
        ).count()
        overdue_tasks = q.filter(
            and_(
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                ComplianceTask.due_date.isnot(None),
                ComplianceTask.due_date < now,
            )
        ).count()

    incidents_open = 0
    if Incident is not None:
        incidents_open = (
            db.query(Incident)
            .filter(
                and_(
                    Incident.company_id == company_id,
                    ~func.lower(Incident.status).in_(("closed", "resolved")),
                )
            )
            .count()
        )

    # Company-scoped finance
    finance = _compute_company_finance(db, company_id, month=None)

    return {
        "systems_cnt": systems_cnt,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "incidents_open": incidents_open,
        "finance": finance,
        "window_days": window_days,
        "since": since.isoformat() + "Z",
        "now": now.isoformat() + "Z",
    }


def systems_table(db: Session, company_id: int) -> List[Dict[str, Any]]:
    """
    Small table of systems (id, name, risk_tier, status, owner_user_id).
    """
    rows = (
        db.query(AISystem)
        .filter(AISystem.company_id == company_id)
        .order_by(AISystem.id.desc())
        .all()
    )
    out: List[Dict[str, Any]] = []
    for s in rows:
        out.append(
            {
                "ai_system_id": s.id,
                "name": s.name,
                "risk_tier": s.risk_tier,
                "status": s.status,
                "owner_user_id": s.owner_user_id,
                "created_at": getattr(s, "created_at", None),
            }
        )
    return out


def overdue_by_owner(
    db: Session, company_id: int, *, limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Simple aggregation: count overdue tasks per owner.
    """
    if ComplianceTask is None:
        return []
    now = datetime.utcnow()
    q = (
        db.query(
            ComplianceTask.owner_user_id.label("owner_user_id"),
            func.count(ComplianceTask.id).label("overdue_cnt"),
        )
        .filter(
            and_(
                ComplianceTask.company_id == company_id,
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                ComplianceTask.due_date.isnot(None),
                ComplianceTask.due_date < now,
            )
        )
        .group_by(ComplianceTask.owner_user_id)
        .order_by(text("overdue_cnt DESC"))
        .limit(limit)
    )
    return [
        {"owner_user_id": r.owner_user_id, "overdue_cnt": int(r.overdue_cnt)} for r in q
    ]


def upcoming_deadlines(
    db: Session, company_id: int, *, in_days: int = 14
) -> List[Dict[str, Any]]:
    """
    Upcoming task deadlines within N days (not including done/cancelled).
    """
    if ComplianceTask is None:
        return []
    now = datetime.utcnow()
    until = now + timedelta(days=in_days)
    q = (
        db.query(ComplianceTask)
        .filter(
            and_(
                ComplianceTask.company_id == company_id,
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                ComplianceTask.due_date.isnot(None),
                ComplianceTask.due_date <= until,
                ComplianceTask.due_date >= now,
            )
        )
        .order_by(ComplianceTask.due_date.asc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": t.id,
            "ai_system_id": t.ai_system_id,
            "title": t.title,
            "due_date": t.due_date,
            "severity": getattr(t, "severity", None),
            "owner_user_id": t.owner_user_id,
            "status": t.status,
        }
        for t in q
    ]


def team_overview(db: Session, company_id: int) -> List[Dict[str, Any]]:
    """
    Very small team snapshot (id, email, role) for members of the company.
    """
    users = (
        db.query(User)
        .filter(User.company_id == company_id)
        .order_by(User.id.asc())
        .all()
    )
    return [
        {"id": u.id, "email": u.email, "role": getattr(u, "role", None)} for u in users
    ]


def reference_breakdown(db: Session, company_id: int) -> List[Dict[str, Any]]:
    """
    If ComplianceTask has 'reference' (e.g., 'Art. 9'), aggregate by reference.
    """
    if ComplianceTask is None:
        return []
    q = (
        db.query(
            ComplianceTask.reference.label("reference"),
            func.count(ComplianceTask.id).label("total"),
            func.sum(
                func.case((func.lower(ComplianceTask.status) == "done", 1), else_=0)
            ).label("done_cnt"),
            func.sum(
                func.case(
                    (
                        and_(
                            ~func.lower(ComplianceTask.status).in_(
                                ("done", "cancelled")
                            ),
                            ComplianceTask.due_date.isnot(None),
                            ComplianceTask.due_date < datetime.utcnow(),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("overdue_cnt"),
        )
        .filter(
            and_(
                ComplianceTask.company_id == company_id,
                ComplianceTask.reference.isnot(None),
            )
        )
        .group_by(ComplianceTask.reference)
        .order_by(text("total DESC"))
        .limit(200)
    )
    return [
        {
            "reference": r.reference,
            "total": int(r.total or 0),
            "done_cnt": int(r.done_cnt or 0),
            "overdue_cnt": int(r.overdue_cnt or 0),
        }
        for r in q
    ]


def company_alerts(
    db: Session, company_id: int, *, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Basic alerts derived from open/overdue tasks and incidents (if model exists).
    """
    alerts: List[Dict[str, Any]] = []

    # Overdue tasks alert
    if ComplianceTask is not None:
        overdue_cnt = (
            db.query(ComplianceTask)
            .filter(
                and_(
                    ComplianceTask.company_id == company_id,
                    ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                    ComplianceTask.due_date.isnot(None),
                    ComplianceTask.due_date < datetime.utcnow(),
                )
            )
            .count()
        )
        if overdue_cnt > 0:
            alerts.append(
                {
                    "type": "tasks_overdue",
                    "severity": "high" if overdue_cnt > 5 else "medium",
                    "message": f"{overdue_cnt} task(s) overdue",
                }
            )

    # Open incidents alert
    if Incident is not None:
        inc_open = (
            db.query(Incident)
            .filter(
                and_(
                    Incident.company_id == company_id,
                    ~func.lower(Incident.status).in_(("closed", "resolved")),
                )
            )
            .count()
        )
        if inc_open > 0:
            alerts.append(
                {
                    "type": "incidents_open",
                    "severity": "medium",
                    "message": f"{inc_open} incident(s) open",
                }
            )

    return alerts[:limit]


def compute_superadmin_overview(db: Session) -> Dict[str, Any]:
    """
    Tiny cross-tenant snapshot (counts) + global finance if available.
    """
    companies = db.query(Company).count()
    systems = db.query(AISystem).count()

    tasks = 0
    if ComplianceTask is not None:
        tasks = db.query(ComplianceTask).count()

    incs = 0
    if Incident is not None:
        incs = db.query(Incident).count()

    out = {
        "companies": companies,
        "ai_systems": systems,
        "tasks": tasks,
        "incidents": incs,
    }

    # Attach global finance (MRR/ARR/ARPU/churn) if the metrics service is available
    try:
        if callable(_compute_metrics_summary):
            snap = _compute_metrics_summary(db, month=None)  # type: ignore
            if isinstance(snap, dict) and "finance" in snap:
                out["finance"] = snap["finance"]
    except Exception:
        # best-effort: never break the overview if metrics fail
        pass

    return out


# -----------------------------
# Regulatory deadlines timeline (+ Company/System compliance_due_date support)
# -----------------------------
def timeline_deadlines(
    db: Session,
    company_id: int,
    *,
    past_days: int = 365,
    future_days: int = 365,
    limit: int = 100,
) -> dict:
    """
    Returns a simple timeline split into 'upcoming' and 'past' regulatory deadlines
    for the given company. Includes both company- and system-scoped deadlines.
    """
    if RegulatoryDeadline is None:
        return {"upcoming": [], "past": [], "window": None}

    now = datetime.utcnow()
    past_after = now - timedelta(days=past_days)
    future_before = now + timedelta(days=future_days)

    base = db.query(RegulatoryDeadline).filter(
        RegulatoryDeadline.company_id == company_id
    )

    # upcoming: [now .. future_before]
    upcoming_rows = (
        base.filter(
            and_(
                RegulatoryDeadline.due_date >= now,
                RegulatoryDeadline.due_date <= future_before,
            )
        )
        .order_by(RegulatoryDeadline.due_date.asc())
        .limit(limit)
        .all()
    )

    # past: [past_after .. now)
    past_rows = (
        base.filter(
            and_(
                RegulatoryDeadline.due_date < now,
                RegulatoryDeadline.due_date >= past_after,
            )
        )
        .order_by(RegulatoryDeadline.due_date.desc())
        .limit(limit)
        .all()
    )

    def _row_to_item(r: Any) -> dict:
        return {
            "id": r.id,
            "type": "deadline",
            "title": getattr(r, "title", None),
            "description": getattr(r, "description", None),
            "due_date": (
                r.due_date.isoformat() if getattr(r, "due_date", None) else None
            ),
            "severity": getattr(r, "severity", None),
            "status": getattr(r, "status", None),
            "kind": getattr(r, "kind", None),  # tolerant if model has it
            "company_id": r.company_id,
            "ai_system_id": getattr(r, "ai_system_id", None),
            "created_at": (
                r.created_at.isoformat() if getattr(r, "created_at", None) else None
            ),
        }

    return {
        "upcoming": [_row_to_item(r) for r in upcoming_rows],
        "past": [_row_to_item(r) for r in past_rows],
        "window": {
            "from": past_after.date().isoformat(),
            "to": future_before.date().isoformat(),
        },
    }


def timeline_for_company(
    db: Session,
    company_id: int,
    *,
    past_days: int = 365,
    future_days: int = 365,
    limit: int = 200,
) -> dict:
    """
    Combined timeline:
      - RegulatoryDeadline rows (company/system scoped)
      - Company.compliance_due_date (if present)
      - Per-system AISystem.compliance_due_date (if present)

    Returns { upcoming: [...], past: [...], window: {from,to} }.
    """
    now = datetime.utcnow()
    base_window = {
        "from": (now - timedelta(days=past_days)).date().isoformat(),
        "to": (now + timedelta(days=future_days)).date().isoformat(),
    }

    # Start with regulatory deadlines (if model exists)
    reg = timeline_deadlines(
        db, company_id, past_days=past_days, future_days=future_days, limit=limit
    )

    items: List[Dict[str, Any]] = []
    items.extend(reg.get("upcoming", []))
    items.extend(reg.get("past", []))

    # Company-level compliance due (tolerant if field doesn't exist)
    company = db.query(Company).filter(Company.id == company_id).first()
    comp_due = getattr(company, "compliance_due_date", None)
    if comp_due:
        items.append(
            {
                "id": -1,  # virtual
                "type": "company_compliance_due",
                "title": "Company compliance deadline",
                "description": None,
                "due_date": comp_due.isoformat(),
                "severity": "high",
                "status": "open",
                "kind": "ai_act_general",
                "company_id": company_id,
                "ai_system_id": None,
                "created_at": None,
            }
        )

    # Per-system compliance due (tolerant if field doesn't exist)
    systems = db.query(AISystem).filter(AISystem.company_id == company_id).all()
    for s in systems:
        s_due = getattr(s, "compliance_due_date", None)
        if s_due:
            items.append(
                {
                    "id": -1000 - int(s.id),  # virtual unique
                    "type": "system_compliance_due",
                    "title": f"AI system compliance deadline: {s.name}",
                    "description": None,
                    "due_date": s_due.isoformat(),
                    "severity": "high",
                    "status": "open",
                    "kind": "ai_act_system",
                    "company_id": company_id,
                    "ai_system_id": s.id,
                    "created_at": None,
                }
            )

    # Partition into upcoming/past
    upcoming: List[Dict[str, Any]] = []
    past: List[Dict[str, Any]] = []
    for it in items:
        try:
            d = datetime.fromisoformat(str(it["due_date"]).replace("Z", ""))
        except Exception:
            continue
        if d >= now:
            upcoming.append(it)
        else:
            past.append(it)

    # Sort
    upcoming.sort(key=lambda x: x.get("due_date") or "")
    past.sort(key=lambda x: x.get("due_date") or "", reverse=True)

    return {"upcoming": upcoming, "past": past, "window": base_window}


# -----------------------------
# High-level assembler for /reports/company/{id}/dashboard
# -----------------------------
def build_company_dashboard(
    db: Session,
    company_id: int,
    *,
    tasks_window_days: int = 30,
    upcoming_tasks_in_days: int = 14,
) -> Dict[str, Any]:
    """
    Bundle the dashboard data structure for the API endpoint.
    """
    kpis = compute_company_kpis(db, company_id, window_days=tasks_window_days)
    systems = systems_table(db, company_id)
    overdue = overdue_by_owner(db, company_id, limit=5)
    upcoming_tasks = upcoming_deadlines(db, company_id, in_days=upcoming_tasks_in_days)
    alerts = company_alerts(db, company_id, limit=10)
    timeline = timeline_for_company(db, company_id, past_days=365, future_days=365)

    return {
        "kpis": kpis,
        "systems": systems,
        "overdue_by_owner": overdue,
        "upcoming_tasks": upcoming_tasks,
        "alerts": alerts,
        "timeline": timeline,
    }


# =============================
# NEW: Compliance snapshot helpers used by compliance_tasks API
# =============================
def compute_compliance_snapshot(db: Session, ai_system_id: int) -> Dict[str, Any]:
    """
    Build a lightweight snapshot for an AI system:
      - total tasks, per-status counts, overdue count, compliance_pct.
    Falls back to zeros if ComplianceTask model is unavailable.

    Note: compliance_pct here is in the 0..1 range (fraction), and callers should
    use 'compliance_status_from_pct' which normalizes either 0..1 or 0..100 inputs.
    """
    if ComplianceTask is None:
        return {
            "ai_system_id": ai_system_id,
            "total": 0,
            "done_cnt": 0,
            "open_cnt": 0,
            "in_progress_cnt": 0,
            "blocked_cnt": 0,
            "postponed_cnt": 0,
            "cancelled_cnt": 0,
            "overdue_cnt": 0,
            "compliance_pct": 0.0,
            "computed_at": datetime.utcnow().isoformat() + "Z",
        }

    now = datetime.utcnow()
    rows = (
        db.query(ComplianceTask)
        .filter(ComplianceTask.ai_system_id == ai_system_id)
        .all()
    )

    total = len(rows)
    done_cnt = 0
    open_cnt = 0
    in_progress_cnt = 0
    blocked_cnt = 0
    postponed_cnt = 0
    cancelled_cnt = 0
    overdue_cnt = 0

    for t in rows:
        s = (getattr(t, "status", "") or "").strip().lower()
        if s == "done":
            done_cnt += 1
        elif s == "in_progress":
            in_progress_cnt += 1
        elif s == "blocked":
            blocked_cnt += 1
        elif s == "postponed":
            postponed_cnt += 1
        elif s == "cancelled":
            cancelled_cnt += 1
        else:
            # treat unknown/None as "open"
            open_cnt += 1

        # overdue = not done/cancelled, has due_date, due_date < now
        due = getattr(t, "due_date", None)
        if s not in {"done", "cancelled"} and due is not None and due < now:
            overdue_cnt += 1

    compliance_pct = (done_cnt / total) if total > 0 else 0.0

    return {
        "ai_system_id": ai_system_id,
        "total": total,
        "done_cnt": done_cnt,
        "open_cnt": open_cnt,
        "in_progress_cnt": in_progress_cnt,
        "blocked_cnt": blocked_cnt,
        "postponed_cnt": postponed_cnt,
        "cancelled_cnt": cancelled_cnt,
        "overdue_cnt": overdue_cnt,
        "compliance_pct": round(compliance_pct, 4),
        "computed_at": datetime.utcnow().isoformat() + "Z",
    }


def compute_compliance_status_for_system(db: Session, ai_system_id: int) -> str:
    """
    Map the current snapshot to a coarse status badge:
      'compliant' | 'at_risk' | 'non_compliant'
    """
    snap = compute_compliance_snapshot(db, ai_system_id)
    pct = float(snap.get("compliance_pct") or 0.0)
    overdue = int(snap.get("overdue_cnt") or 0)
    return compliance_status_from_pct(pct, overdue)
