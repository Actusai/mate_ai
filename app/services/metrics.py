# app/services/metrics.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text

# --- Tolerant imports / fallbacks --------------------------------------------
try:
    # if your reporting service exposes this helper
    from app.services.reporting import compute_compliance_status_for_system  # type: ignore
except Exception:  # pragma: no cover

    def compute_compliance_status_for_system(db: Session, ai_system_id: int) -> str:
        # Minimal fallback: treat systems with no overdue tasks as compliant
        try:
            row = (
                db.execute(
                    text(
                        """
                    SELECT COUNT(1) AS overdue
                    FROM compliance_tasks
                    WHERE ai_system_id = :aid
                      AND due_date IS NOT NULL
                      AND (status IS NULL OR LOWER(status) NOT IN ('done','cancelled'))
                      AND due_date < datetime('now')
                """
                    ),
                    {"aid": ai_system_id},
                )
                .mappings()
                .first()
            )
            return "compliant" if int(row["overdue"] or 0) == 0 else "non_compliant"
        except Exception:
            return "unknown"


try:
    from app.services.compliance import fria_required_for_system, get_fria_status  # type: ignore
except Exception:  # pragma: no cover

    def fria_required_for_system(system_like) -> bool:  # type: ignore
        # Minimal heuristic fallback
        rt = (getattr(system_like, "risk_tier", "") or "").lower()
        return rt in {"high_risk", "high-risk", "high"}

    def get_fria_status(db: Session, ai_system_id: int) -> Dict[str, Any]:  # type: ignore
        return {"status": "unknown"}


# ---------- small utils ----------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _month_bounds(yyyy_mm: Optional[str]) -> Tuple[datetime, datetime]:
    """
    Return [start, end) UTC-naive bounds for a calendar month (YYYY-MM).
    """
    if yyyy_mm:
        y, m = yyyy_mm.split("-")
        y, m = int(y), int(m)
        start = datetime(y, m, 1)
    else:
        now = _utcnow()
        start = datetime(now.year, now.month, 1)
    # first day of next month
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)
    return start, end


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


# ---------- finance metrics ----------
def _compute_finance(db: Session, yyyy_mm: Optional[str]) -> Dict[str, Any]:
    """
    Finance snapshot for SuperAdmin dashboard:
      - MRR: sum of active subscriptions now
      - ARR: 12 * MRR
      - ARPU: MRR / #active companies
      - Logo churn % (for the month window)

    Works with (tolerant to schema variants):
      company_packages: starts_at|start_date, ends_at|end_date, status?, billing_term?,
                        unit_price_month?, unit_price_year?
      packages: price_month?, price_year?
    """
    import os

    now = _utcnow()
    month_start, month_end = _month_bounds(yyyy_mm)

    if not _table_exists(db, "company_packages"):
        return {
            "mrr": 0.0,
            "arr": 0.0,
            "arpu": 0.0,
            "active_companies": 0,
            "logo_churn_pct": 0.0,
            "month": month_start.strftime("%Y-%m"),
        }

    # Detect columns (tolerant to schema variants)
    cp_has_status = _column_exists(db, "company_packages", "status")
    cp_has_bterm = _column_exists(db, "company_packages", "billing_term")
    cp_has_u_pm = _column_exists(db, "company_packages", "unit_price_month")
    cp_has_u_py = _column_exists(db, "company_packages", "unit_price_year")
    cp_has_starts = _column_exists(db, "company_packages", "starts_at")
    cp_has_startd = _column_exists(db, "company_packages", "start_date")
    cp_has_ends = _column_exists(db, "company_packages", "ends_at")
    cp_has_endd = _column_exists(db, "company_packages", "end_date")

    starts_col = (
        "starts_at" if cp_has_starts else ("start_date" if cp_has_startd else None)
    )
    ends_col = "ends_at" if cp_has_ends else ("end_date" if cp_has_endd else None)

    has_pkg_table = _table_exists(db, "packages")
    pkg_has_pm = (
        _column_exists(db, "packages", "price_month") if has_pkg_table else False
    )
    pkg_has_py = (
        _column_exists(db, "packages", "price_year") if has_pkg_table else False
    )

    # Defaults if prices missing
    default_month_price = float(os.getenv("DEFAULT_MONTHLY_PRICE", "0") or 0)
    default_year_price = float(os.getenv("DEFAULT_YEARLY_PRICE", "0") or 0)

    # Build price expression (monthly equivalent)
    # monthly: unit_month if set else package.price_month else default
    # yearly:  (unit_year  if set else package.price_year  else default) / 12
    if cp_has_bterm:
        monthly_price_expr = (
            f"COALESCE(cp.unit_price_month, {'p.price_month' if pkg_has_pm else str(default_month_price)})"
            if cp_has_u_pm
            else (f"{'p.price_month' if pkg_has_pm else str(default_month_price)}")
        )
        yearly_price_expr = (
            f"COALESCE(cp.unit_price_year, {'p.price_year' if pkg_has_py else str(default_year_price)})/12.0"
            if cp_has_u_py
            else (f"{'p.price_year' if pkg_has_py else str(default_year_price)}/12.0")
        )
        monthly_equiv_expr = f"""
            CASE
              WHEN LOWER(COALESCE(cp.billing_term,'')) = 'monthly' THEN ({monthly_price_expr})
              WHEN LOWER(COALESCE(cp.billing_term,'')) = 'yearly'  THEN ({yearly_price_expr})
              ELSE 0
            END
        """
    else:
        # If no billing_term column, treat every sub as monthly
        monthly_equiv_expr = (
            f"COALESCE(cp.unit_price_month, {'p.price_month' if pkg_has_pm else str(default_month_price)})"
            if cp_has_u_pm
            else (f"{'p.price_month' if pkg_has_pm else str(default_month_price)}")
        )

    join_pkg = "JOIN packages p ON p.id = cp.package_id" if has_pkg_table else ""
    status_clause = "AND LOWER(cp.status) = 'active'" if cp_has_status else ""

    # Active subscriptions NOW → (starts <= now) AND (ends IS NULL OR ends > now)
    time_predicates = []
    if starts_col:
        time_predicates.append(f"cp.{starts_col} <= :now")
    if ends_col:
        time_predicates.append(f"(cp.{ends_col} IS NULL OR cp.{ends_col} > :now)")
    time_clause = " AND ".join(time_predicates) if time_predicates else "1=1"

    active_sql = f"""
        SELECT cp.company_id AS company_id,
               ({monthly_equiv_expr}) AS m_price
        FROM company_packages cp
        {join_pkg}
        WHERE {time_clause}
        {status_clause}
    """
    active_rows = (
        db.execute(
            text(active_sql),
            {"now": now.isoformat(sep=" ")},
        )
        .mappings()
        .all()
    )

    mrr = float(sum(float(r["m_price"] or 0) for r in active_rows))
    arr = mrr * 12.0
    active_companies = (
        len({int(r["company_id"]) for r in active_rows}) if active_rows else 0
    )
    arpu = (mrr / active_companies) if active_companies > 0 else 0.0

    # Logo churn (companies whose sub ended in [month_start, month_end))
    churned_companies = 0
    if ends_col:
        ended_rows = (
            db.execute(
                text(
                    f"""
                SELECT DISTINCT cp.company_id AS cid
                FROM company_packages cp
                WHERE cp.{ends_col} >= :mstart AND cp.{ends_col} < :mend
            """
                ),
                {
                    "mstart": month_start.isoformat(sep=" "),
                    "mend": month_end.isoformat(sep=" "),
                },
            )
            .mappings()
            .all()
        )
        churned_companies = len(ended_rows)

    # Active at start-of-month (denominator)
    active_at_start = 0
    if starts_col:
        preds = [f"cp.{starts_col} <= :mstart"]
        if ends_col:
            preds.append(f"(cp.{ends_col} IS NULL OR cp.{ends_col} > :mstart)")
        if cp_has_status:
            preds.append("LOWER(cp.status) = 'active'")
        active_start_rows = (
            db.execute(
                text(
                    f"SELECT DISTINCT cp.company_id AS cid FROM company_packages cp WHERE {' AND '.join(preds)}"
                ),
                {"mstart": month_start.isoformat(sep=" ")},
            )
            .mappings()
            .all()
        )
        active_at_start = len(active_start_rows)

    logo_churn_pct = (
        (churned_companies / active_at_start) * 100.0 if active_at_start > 0 else 0.0
    )

    return {
        "mrr": round(mrr, 2),
        "arr": round(arr, 2),
        "arpu": round(arpu, 2),
        "active_companies": active_companies,
        "logo_churn_pct": round(logo_churn_pct, 2),
        "month": month_start.strftime("%Y-%m"),
    }


# ---------- user metrics ----------
def _compute_users(db: Session, yyyy_mm: Optional[str]) -> Dict[str, Any]:
    if not _table_exists(db, "users"):
        return {"new_users_month": 0, "mau": 0, "dau": 0}

    has_created = _column_exists(db, "users", "created_at")
    has_last_log = _column_exists(db, "users", "last_login_at")

    month_start, month_end = _month_bounds(yyyy_mm)
    now = _utcnow()
    day_start = datetime(now.year, now.month, now.day)

    # New users in month
    new_users_month = 0
    if has_created:
        row = (
            db.execute(
                text(
                    "SELECT COUNT(1) AS cnt FROM users WHERE created_at >= :s AND created_at < :e"
                ),
                {
                    "s": month_start.isoformat(sep=" "),
                    "e": month_end.isoformat(sep=" "),
                },
            )
            .mappings()
            .first()
        )
        new_users_month = int(row["cnt"] or 0)

    # MAU (approx via last_login_at in last 30d)
    mau = 0
    if has_last_log:
        row = (
            db.execute(
                text("SELECT COUNT(1) AS cnt FROM users WHERE last_login_at >= :since"),
                {"since": (now - timedelta(days=30)).isoformat(sep=" ")},
            )
            .mappings()
            .first()
        )
        mau = int(row["cnt"] or 0)

    # DAU (last_login_at today)
    dau = 0
    if has_last_log:
        row = (
            db.execute(
                text("SELECT COUNT(1) AS cnt FROM users WHERE last_login_at >= :today"),
                {"today": day_start.isoformat(sep=" ")},
            )
            .mappings()
            .first()
        )
        dau = int(row["cnt"] or 0)

    return {"new_users_month": new_users_month, "mau": mau, "dau": dau}


# ---------- AI metrics ----------
def _compute_ai(db: Session, yyyy_mm: Optional[str]) -> Dict[str, Any]:
    if not _table_exists(db, "ai_systems"):
        return {
            "new_systems_month": 0,
            "risk_tier_distribution": {},
            "green_rate_pct": 0.0,
            "fria_required": 0,
            "fria_completed": 0,
            "tasks_done": 0,
            "tasks_pending": 0,
        }

    month_start, month_end = _month_bounds(yyyy_mm)
    has_created = _column_exists(db, "ai_systems", "created_at")

    # New systems in month
    new_systems_month = 0
    if has_created:
        row = (
            db.execute(
                text(
                    "SELECT COUNT(1) AS cnt FROM ai_systems WHERE created_at >= :s AND created_at < :e"
                ),
                {
                    "s": month_start.isoformat(sep=" "),
                    "e": month_end.isoformat(sep=" "),
                },
            )
            .mappings()
            .first()
        )
        new_systems_month = int(row["cnt"] or 0)

    # Risk tier distribution
    dist_rows = (
        db.execute(
            text(
                "SELECT LOWER(COALESCE(risk_tier,'unknown')) AS rt, COUNT(1) AS n FROM ai_systems GROUP BY rt"
            )
        )
        .mappings()
        .all()
    )
    risk_tier_distribution = {str(r["rt"]): int(r["n"]) for r in dist_rows}

    # Green rate:
    # Prefer view vw_system_compliance (compliance_pct + overdue_cnt) with 80%+ & 0 overdue == compliant.
    total_row = (
        db.execute(text("SELECT COUNT(1) AS cnt FROM ai_systems")).mappings().first()
    )
    total = int(total_row["cnt"] or 0)
    green_rate_pct = 0.0
    if total > 0:
        if _table_exists(db, "vw_system_compliance"):
            rows = (
                db.execute(
                    text("SELECT compliance_pct, overdue_cnt FROM vw_system_compliance")
                )
                .mappings()
                .all()
            )
            compliant = 0
            for r in rows:
                pct = float(r.get("compliance_pct") or 0.0)
                overdue = int(r.get("overdue_cnt") or 0)
                if pct >= 0.80 and overdue == 0:
                    compliant += 1
            green_rate_pct = round((compliant / total) * 100.0, 2)
        else:
            # fallback – compute per system (slower but safe)
            ids = [
                int(r["id"])
                for r in db.execute(text("SELECT id FROM ai_systems")).mappings().all()
            ]
            compliant = 0
            for sid in ids:
                if compute_compliance_status_for_system(db, sid) == "compliant":
                    compliant += 1
            green_rate_pct = round((compliant / total) * 100.0, 2)

    # FRIA required vs completed
    fria_required = 0
    fria_completed = 0
    sys_rows = db.execute(text("SELECT id, risk_tier FROM ai_systems")).mappings().all()

    class _S:
        def __init__(self, i: int, rt: Optional[str]):
            self.id, self.risk_tier = i, rt

    for r in sys_rows:
        s = _S(int(r["id"]), r.get("risk_tier"))
        try:
            if fria_required_for_system(s):  # type: ignore
                fria_required += 1
                st = (get_fria_status(db, s.id).get("status") or "").lower()  # type: ignore
                if st == "completed":
                    fria_completed += 1
        except Exception:
            # be defensive; don't break snapshot
            pass

    # Tasks done vs pending
    tasks_done = 0
    tasks_pending = 0
    if _table_exists(db, "compliance_tasks"):
        # done
        row = (
            db.execute(
                text(
                    "SELECT COUNT(1) AS cnt FROM compliance_tasks WHERE LOWER(COALESCE(status,'')) = 'done'"
                )
            )
            .mappings()
            .first()
        )
        tasks_done = int(row["cnt"] or 0)

        # pending (not done/cancelled)
        row = (
            db.execute(
                text(
                    """
                SELECT COUNT(1) AS cnt
                FROM compliance_tasks
                WHERE (status IS NULL OR LOWER(status) NOT IN ('done','cancelled'))
            """
                )
            )
            .mappings()
            .first()
        )
        tasks_pending = int(row["cnt"] or 0)

    return {
        "new_systems_month": new_systems_month,
        "risk_tier_distribution": risk_tier_distribution,
        "green_rate_pct": green_rate_pct,
        "fria_required": fria_required,
        "fria_completed": fria_completed,
        "tasks_done": tasks_done,
        "tasks_pending": tasks_pending,
    }


# ---------- public API ----------
def compute_metrics_summary(
    db: Session, *, month: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build a SuperAdmin-only metrics snapshot for dashboards.
    month: 'YYYY-MM' (optional). Used for "new this month" & churn window.
    """
    return {
        "finance": _compute_finance(db, month),
        "users": _compute_users(db, month),
        "ai": _compute_ai(db, month),
        "generated_at": _utcnow().isoformat() + "Z",
    }
