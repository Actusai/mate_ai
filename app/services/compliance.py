# app/services/compliance.py
from __future__ import annotations
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text

# ---- Derived compliance status iz metrika ----
def compliance_status_from_metrics(
    compliance_pct: Optional[float],
    overdue_cnt: Optional[int],
) -> str:
    """
    Minimalna, deterministička logika:
      - ako postoji ijedan overdue -> non_compliant
      - ako nema metrika -> unknown
      - ako 100% done -> compliant
      - inače -> at_risk
    """
    try:
        if overdue_cnt is not None and int(overdue_cnt) > 0:
            return "non_compliant"
        if compliance_pct is None:
            return "unknown"
        if float(compliance_pct) >= 99.999:
            return "compliant"
        return "at_risk"
    except Exception:
        return "unknown"

def compute_effective_risk(
    risk_tier: Optional[str],
    compliance_status: str,
) -> str:
    """
    Jednostavan badge za UI:
      - non_compliant -> critical
      - high_risk & (at_risk|unknown) -> critical
      - high_risk & compliant -> warning
      - at_risk -> warning
      - inače -> ok
    """
    rt = (risk_tier or "").lower()
    cs = (compliance_status or "").lower()

    if cs == "non_compliant":
        return "critical"

    if rt == "high_risk":
        return "critical" if cs in {"at_risk", "unknown"} else "warning"

    if cs in {"at_risk", "unknown"}:
        return "warning"

    return "ok"

# ---- Dohvat iz view-a, fallback na tasks tablicu (ako view nije dostupan) ----
def get_system_compliance_status(db: Session, system_id: int) -> Dict[str, Any]:
    """
    Vrati {compliance_pct, overdue_cnt, compliance_status}.
    Preferira 'vw_system_compliance'; ako ne postoji red, fallbackom izračuna iz 'compliance_tasks'.
    """
    row = db.execute(
        text("""
            SELECT compliance_pct, overdue_cnt
            FROM vw_system_compliance
            WHERE ai_system_id = :aid
            LIMIT 1
        """),
        {"aid": system_id},
    ).mappings().first()

    if row:
        cp = row.get("compliance_pct")
        od = row.get("overdue_cnt")
        return {
            "compliance_pct": cp,
            "overdue_cnt": od,
            "compliance_status": compliance_status_from_metrics(cp, od),
        }

    # Fallback: izračun iz compliance_tasks
    # done% = done / total (status='done'); overdue = due_date < today AND status != 'done'
    # Napomena: ovo je grubi fallback, dovoljno za slučaj bez view-a.
    fb = db.execute(
        text("""
            WITH
            totals AS (
              SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_cnt
              FROM compliance_tasks
              WHERE ai_system_id = :aid
            ),
            overdue AS (
              SELECT
                SUM(CASE WHEN due_date IS NOT NULL
                          AND date(due_date) < date('now')
                          AND status <> 'done' THEN 1 ELSE 0 END) AS overdue_cnt
              FROM compliance_tasks
              WHERE ai_system_id = :aid
            )
            SELECT
              t.total AS total,
              t.done_cnt AS done_cnt,
              o.overdue_cnt AS overdue_cnt
            FROM totals t, overdue o
        """),
        {"aid": system_id},
    ).mappings().first()

    if not fb:
        return {"compliance_pct": None, "overdue_cnt": None, "compliance_status": "unknown"}

    total = int(fb["total"] or 0)
    done_cnt = int(fb["done_cnt"] or 0)
    overdue_cnt = int(fb["overdue_cnt"] or 0)
    cp = (100.0 * done_cnt / total) if total > 0 else None
    return {
        "compliance_pct": cp,
        "overdue_cnt": overdue_cnt,
        "compliance_status": compliance_status_from_metrics(cp, overdue_cnt),
    }