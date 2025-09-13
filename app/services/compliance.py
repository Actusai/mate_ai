# app/services/compliance.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, or_, text

# Tolerant imports: models may vary across environments
try:
    from app.models.ai_system import AISystem  # pragma: no cover
except Exception:  # pragma: no cover
    AISystem = None  # type: ignore

try:
    from app.models.company import Company  # pragma: no cover
except Exception:  # pragma: no cover
    Company = None  # type: ignore

try:
    from app.models.document import Document  # pragma: no cover
except Exception:  # pragma: no cover
    Document = None  # type: ignore

# Optional models (guarded)
try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:  # pragma: no cover
    ComplianceTask = None  # type: ignore

try:
    from app.models.incident import Incident  # pragma: no cover
except Exception:  # pragma: no cover
    Incident = None  # type: ignore


# =========================
# Normalization helpers
# =========================
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _is_trueish(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


# =========================
# Legacy/Derived compliance helpers (kept for compatibility)
# =========================
def compliance_status_from_metrics(
    compliance_pct: Optional[float],
    overdue_cnt: Optional[int],
) -> str:
    """
    Deterministic, minimal logic:
      - if there is any overdue -> 'non_compliant'
      - if metrics are missing -> 'unknown'
      - if 100% done -> 'compliant'
      - otherwise -> 'at_risk'
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
    Small UI badge for effective risk:
      - 'non_compliant' -> 'critical'
      - 'high_risk' & (at_risk|unknown) -> 'critical'
      - 'high_risk' & compliant -> 'warning'
      - 'at_risk' or 'unknown' -> 'warning'
      - otherwise -> 'ok'
    """
    rt = (risk_tier or "").lower().replace("-", "_")
    cs = (compliance_status or "").lower()

    if cs == "non_compliant":
        return "critical"

    if rt == "high_risk":
        return "critical" if cs in {"at_risk", "unknown"} else "warning"

    if cs in {"at_risk", "unknown"}:
        return "warning"

    return "ok"


def get_system_compliance_status(db: Session, system_id: int) -> Dict[str, Any]:
    """
    Return {compliance_pct, overdue_cnt, compliance_status}.
    Prefer 'vw_system_compliance'; if none, fallback to compute from 'compliance_tasks'.
    """
    row = (
        db.execute(
            text(
                """
            SELECT compliance_pct, overdue_cnt
            FROM vw_system_compliance
            WHERE ai_system_id = :aid
            LIMIT 1
            """
            ),
            {"aid": system_id},
        )
        .mappings()
        .first()
    )

    if row:
        cp = row.get("compliance_pct")
        od = row.get("overdue_cnt")
        return {
            "compliance_pct": cp,
            "overdue_cnt": od,
            "compliance_status": compliance_status_from_metrics(cp, od),
        }

    # Fallback from compliance_tasks (done% = done/total; overdue = due_date < today AND status <> 'done')
    fb = (
        db.execute(
            text(
                """
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
            """
            ),
            {"aid": system_id},
        )
        .mappings()
        .first()
    )

    if not fb:
        return {
            "compliance_pct": None,
            "overdue_cnt": None,
            "compliance_status": "unknown",
        }

    total = int(fb["total"] or 0)
    done_cnt = int(fb["done_cnt"] or 0)
    overdue_cnt = int(fb["overdue_cnt"] or 0)
    cp = (100.0 * done_cnt / total) if total > 0 else None
    return {
        "compliance_pct": cp,
        "overdue_cnt": overdue_cnt,
        "compliance_status": compliance_status_from_metrics(cp, overdue_cnt),
    }


# =========================
# FRIA helpers
# =========================
_FRIA_DOC_TYPES: set[str] = {"fria", "fria_report", "fria_pdf"}


def fria_required_for_system(sys_like: Any) -> bool:
    """
    Return True if the system requires a FRIA based on its inherent risk tier.
    High-risk systems (according to AI Act taxonomy) require FRIA.
    """
    tier = _norm(getattr(sys_like, "risk_tier", None)).replace("-", "_")
    return tier in {"high", "high_risk"}


def _classify_docs_status(
    rows: List[Any],
) -> Tuple[str, Optional[datetime], Dict[str, int]]:
    """
    Map a set of document rows (FRIA docs) to a coarse status:
      - completed (if any doc has status in {complete, completed, approved})
      - in_progress (if none is completed but any in {in_progress, draft, pending_review})
      - missing (no rows)
    Return (status, latest_updated_at, counters_by_status)
    """
    if not rows:
        return "missing", None, {}

    counters: Dict[str, int] = {}
    latest_dt: Optional[datetime] = None

    def bump(key: str) -> None:
        counters[key] = counters.get(key, 0) + 1

    completed_markers = {"complete", "completed", "approved"}
    progress_markers = {
        "in_progress",
        "in-progress",
        "draft",
        "pending_review",
        "pending-approval",
        "pending",
    }

    has_completed = False
    has_progress = False

    for d in rows:
        st_raw = getattr(d, "status", None)
        st = _norm(st_raw) or "unknown"
        bump(st)
        if st in completed_markers:
            has_completed = True
        elif st in progress_markers:
            has_progress = True

        upd = getattr(d, "updated_at", None) or getattr(d, "created_at", None)
        if isinstance(upd, datetime):
            latest_dt = upd if (latest_dt is None or upd > latest_dt) else latest_dt

    if has_completed:
        return "completed", latest_dt, counters
    if has_progress:
        return "in_progress", latest_dt, counters
    return "unknown", latest_dt, counters


def _latest_doc_by_type(
    db: Session,
    *,
    company_id: int,
    ai_system_id: int,
    types: set[str],
) -> Optional[Document]:
    if Document is None:
        return None
    q = (
        db.query(Document)
        .filter(
            Document.company_id == company_id,
            Document.ai_system_id == ai_system_id,
            Document.type.in_(list(types)),
        )
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return q.first()


def get_fria_status(db: Session, ai_system_id: int) -> Dict[str, Any]:
    """
    Inspect documents for the AI system and infer FRIA readiness status.
    Looks at Document.type in _FRIA_DOC_TYPES.
    Returns:
      {
        "ai_system_id": int,
        "required": bool | None,
        "status": "missing"|"in_progress"|"completed"|"unknown",
        "document_id": int | None,
        "document_status": str | None,
        "latest_update_at": ISO8601 or None,
        "counts": {status: n, ...}
      }
    """
    # Determine requirement (if AISystem available)
    required: Optional[bool] = None
    if AISystem is not None:
        sys = db.query(AISystem).filter(AISystem.id == ai_system_id).first()
        if sys:
            required = fria_required_for_system(sys)

    if Document is None:
        return {
            "ai_system_id": ai_system_id,
            "required": required,
            "status": "unknown",
            "document_id": None,
            "document_status": None,
            "latest_update_at": None,
            "counts": {},
        }

    rows = (
        db.query(Document)
        .filter(
            Document.ai_system_id == ai_system_id,
            Document.type.in_(list(_FRIA_DOC_TYPES)),
        )
        .order_by(
            Document.updated_at.desc().nullslast(),
            Document.created_at.desc().nullslast(),
            Document.id.desc(),
        )
        .all()
    )
    st, latest_dt, counters = _classify_docs_status(rows)

    doc = rows[0] if rows else None
    doc_status = getattr(doc, "status", None) if doc else None
    doc_id = getattr(doc, "id", None) if doc else None

    # If no doc and ComplianceTask exists, try to infer "in_progress" from tasks that mention FRIA
    if not rows and ComplianceTask is not None:
        conds = [func.lower(ComplianceTask.title).like("%fria%")]
        if hasattr(ComplianceTask, "reference"):
            conds.append(func.lower(ComplianceTask.reference).like("%fria%"))
        task = (
            db.query(ComplianceTask)
            .filter(
                ComplianceTask.ai_system_id == ai_system_id,
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                or_(*conds),
            )
            .order_by(ComplianceTask.id.desc())
            .first()
        )
        if task:
            st = "in_progress"

    return {
        "ai_system_id": ai_system_id,
        "required": required,
        "status": (
            st
            if rows or required is None
            else ("not_required" if required is False else st)
        ),
        "document_id": int(doc_id) if doc_id is not None else None,
        "document_status": doc_status,
        "latest_update_at": (latest_dt.isoformat() + "Z") if latest_dt else None,
        "counts": counters,
    }


# =========================
# AR (Authorized Representative) helpers
# =========================
def _company_is_ar(company: Any) -> bool:
    """
    Decide if the company acts as Authorized Representative.
    We use a tolerant check on company_type or boolean flags.
    """
    if company is None:
        return False
    # 1) explicit boolean flag if present
    if hasattr(company, "is_authorized_representative") and _is_trueish(
        getattr(company, "is_authorized_representative")
    ):
        return True
    # 2) company_type taxonomy fallback
    ctype = _norm(getattr(company, "company_type", None))
    return ctype in {"ar", "authorized_representative", "provider_ar", "ar_provider"}


def get_ar_appointment_status(db: Session, company_id: int) -> Dict[str, Any]:
    """
    Check if the company has an AR appointment document present and completed.
    Looks for Document(type='ar_appointment').
    Returns a structure similar to FRIA status.
    """
    if Company is None or Document is None:
        return {
            "company_id": company_id,
            "status": "unknown",
            "latest_update_at": None,
            "counts": {},
        }

    company = db.query(Company).filter(Company.id == company_id).first()
    if not company or not _company_is_ar(company):
        # Not required for non-AR companies
        return {
            "company_id": company_id,
            "status": "not_required",
            "latest_update_at": None,
            "counts": {},
        }

    rows = (
        db.query(Document)
        .filter(
            Document.company_id == company_id,
            Document.ai_system_id.is_(None),  # company-scoped
            Document.type == "ar_appointment",
        )
        .order_by(
            Document.updated_at.desc().nullslast(),
            Document.created_at.desc().nullslast(),
            Document.id.desc(),
        )
        .all()
    )
    st, latest_dt, counters = _classify_docs_status(rows)
    return {
        "company_id": company_id,
        "status": st if rows else "missing",
        "latest_update_at": (latest_dt.isoformat() + "Z") if latest_dt else None,
        "counts": counters,
    }


# =========================
# Banners / Readiness summaries
# =========================
def compute_system_banner(db: Session, ai_system_id: int) -> Optional[Dict[str, Any]]:
    """
    Compute a lightweight banner for UI based on FRIA requirements.
    Returns a dict with 'severity','title','message' or None if no banner is needed.
    """
    if AISystem is None:
        return None
    sys_obj = db.query(AISystem).filter(AISystem.id == ai_system_id).first()
    if not sys_obj:
        return None

    if not fria_required_for_system(sys_obj):
        return None  # banner only for high-risk

    fria = get_fria_status(db, ai_system_id)
    st = _norm(fria.get("status"))

    if st in {"missing", "unknown"}:
        return {
            "severity": "warning",
            "title": "FRIA required",
            "message": "This AI system is classified as high-risk. A Fundamental Rights Impact Assessment (FRIA) is required and has not been uploaded.",
        }

    if st == "in_progress":
        return {
            "severity": "info",
            "title": "FRIA in progress",
            "message": "FRIA has been started but is not completed yet. Please finalize and upload the final document.",
        }

    # completed -> no banner
    return None


def get_required_docs_flags(
    db: Session,
    *,
    company_id: int,
    ai_system_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    A compact summary of required docs checks for UI/API.
    - For company: AR appointment if company acts as AR.
    - For system: FRIA if high-risk.
    """
    out: Dict[str, Any] = {"company": {}, "system": {}}

    # Company-level AR appointment
    ar = get_ar_appointment_status(db, company_id)
    out["company"]["ar_required"] = ar.get("status") not in {
        "not_required",
        "completed",
    }
    out["company"]["ar_status"] = ar.get("status")

    # System-level FRIA
    if ai_system_id is not None:
        fria = get_fria_status(db, ai_system_id)
        sys_req = None
        if AISystem is not None:
            sys_obj = db.query(AISystem).filter(AISystem.id == ai_system_id).first()
            if sys_obj:
                sys_req = fria_required_for_system(sys_obj)
        out["system"]["fria_required"] = (
            bool(sys_req) and _norm(fria.get("status")) != "completed"
        )
        out["system"]["fria_status"] = fria.get("status")

    return out


# =========================
# AR readiness aggregator (for supervision dashboards/workflows)
# =========================
def get_ar_readiness(db: Session, system_id: int) -> Dict[str, Any]:
    """
    Aggregate readiness snapshot:
      - FRIA status
      - EU Conformity Report present (type='doc_eu_conformity')
      - Technical Documentation Pack present (type='doc_pack_zip')
      - Incidents (any open high/critical)
      - Simple 'ready_for_supervision' boolean + blockers/hints
    """
    if AISystem is None:
        return {"ready_for_supervision": False, "reason": "system_model_not_available"}

    system = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not system:
        return {"ready_for_supervision": False, "reason": "system_not_found"}

    # FRIA
    fria = get_fria_status(db, system.id)

    # Conformity doc
    conformity_doc = _latest_doc_by_type(
        db,
        company_id=system.company_id,
        ai_system_id=system.id,
        types={"doc_eu_conformity"},
    )

    # Doc pack (ZIP)
    doc_pack = _latest_doc_by_type(
        db,
        company_id=system.company_id,
        ai_system_id=system.id,
        types={"doc_pack_zip"},
    )

    # Incidents
    high_open_cnt = 0
    last_incident: Optional[Dict[str, Any]] = None
    if Incident is not None:
        high_open_cnt = (
            db.query(Incident)
            .filter(
                Incident.ai_system_id == system.id,
                ~func.lower(Incident.status).in_(("closed", "resolved")),
                func.lower(Incident.severity).in_(("critical", "high")),
            )
            .count()
        )

        li = (
            db.query(Incident)
            .filter(Incident.ai_system_id == system.id)
            .order_by(
                getattr(Incident, "occurred_at", None).desc().nulls_last(),
                Incident.id.desc(),
            )
            .first()
        )
        if li:
            last_incident = {
                "id": li.id,
                "severity": getattr(li, "severity", None),
                "status": getattr(li, "status", None),
                "occurred_at": getattr(li, "occurred_at", None),
            }

    blockers: List[str] = []
    if (fria.get("required") is True) and (fria.get("status") != "completed"):
        blockers.append("FRIA is required but not completed.")
    if conformity_doc is None:
        blockers.append("EU Conformity Report has not been generated.")
    if high_open_cnt > 0:
        blockers.append("There are open high/critical incidents.")

    hints: List[str] = []
    if doc_pack is None:
        hints.append("Create a Technical Documentation Pack to streamline submissions.")

    ready = len(blockers) == 0

    return {
        "ai_system_id": system.id,
        "company_id": system.company_id,
        "fria": fria,
        "conformity_doc": {
            "document_id": getattr(conformity_doc, "id", None),
            "status": (
                getattr(conformity_doc, "status", None) if conformity_doc else None
            ),
        },
        "doc_pack": {
            "document_id": getattr(doc_pack, "id", None),
            "status": getattr(doc_pack, "status", None) if doc_pack else None,
        },
        "incidents": {
            "open_high_or_critical": high_open_cnt,
            "last": last_incident,
        },
        "ready_for_supervision": ready,
        "blockers": blockers,
        "hints": hints,
    }
