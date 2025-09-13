# app/services/compliance_fria.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple, List

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from app.models.ai_system import AISystem
from app.models.company import Company
from app.models.document import Document
from app.services.audit import audit_log  # audit best-effort

try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:
    ComplianceTask = None  # type: ignore

try:
    from app.crud.compliance_task import (
        create_task as crud_create_task,
    )  # pragma: no cover
    from app.schemas.compliance_task import ComplianceTaskCreate  # pragma: no cover
except Exception:
    crud_create_task = None  # type: ignore
    ComplianceTaskCreate = None  # type: ignore


FRIA_DOC_TYPES = {"fria", "fria_report", "fundamental_rights_impact_assessment"}
FRIA_TASK_KEYWORDS = ("fundamental rights impact", "fria")


def fria_is_required(system: AISystem) -> bool:
    """
    Simplified rule: FRIA is required for 'high' risk systems (Annex III surrogate).
    Extend as needed.
    """
    tier = (getattr(system, "risk_tier", "") or "").lower()
    return tier in {"high", "high_risk", "high-risk"}


def _latest_due_date(
    system: AISystem, company: Optional[Company]
) -> Optional[datetime]:
    return getattr(system, "compliance_due_date", None) or getattr(
        company, "compliance_due_date", None
    )


def fria_documents(db: Session, system_id: int) -> List[Document]:
    q = (
        db.query(Document)
        .filter(
            and_(
                Document.ai_system_id == system_id,
                or_(
                    Document.type.in_(FRIA_DOC_TYPES),
                    func.lower(Document.name).like("%fria%"),
                    func.lower(Document.name).like("%fundamental rights impact%"),
                ),
            )
        )
        .order_by(Document.id.desc())
    )
    return q.all()


def latest_fria_document(db: Session, system_id: int) -> Optional[Document]:
    q = (
        db.query(Document)
        .filter(
            and_(
                Document.ai_system_id == system_id,
                or_(
                    Document.type.in_(FRIA_DOC_TYPES),
                    func.lower(Document.name).like("%fria%"),
                    func.lower(Document.name).like("%fundamental rights impact%"),
                ),
            )
        )
        .order_by(Document.id.desc())
    )
    return q.first()


def fria_task_existing(db: Session, system_id: int) -> Optional["ComplianceTask"]:
    if ComplianceTask is None:
        return None
    q = (
        db.query(ComplianceTask)
        .filter(
            and_(
                ComplianceTask.ai_system_id == system_id,
                or_(
                    func.lower(ComplianceTask.title).like("%fria%"),
                    func.lower(ComplianceTask.title).like(
                        "%fundamental rights impact%"
                    ),
                    func.lower(ComplianceTask.reference).like("%fria%"),
                ),
            )
        )
        .order_by(ComplianceTask.id.desc())
    )
    return q.first()


def ensure_fria_task(
    db: Session, *, system_id: int, created_by_user_id: Optional[int]
) -> Tuple[bool, Optional[int]]:
    """
    Create a FRIA task if it doesn't exist.
    Returns (created, task_id).
    """
    system: AISystem | None = (
        db.query(AISystem).filter(AISystem.id == system_id).first()
    )
    if not system:
        raise ValueError("AI system not found")

    company: Company | None = (
        db.query(Company).filter(Company.id == system.company_id).first()
    )

    if not fria_is_required(system):
        return (False, None)

    existing = fria_task_existing(db, system_id)
    if existing:
        return (False, int(existing.id))

    if crud_create_task is None or ComplianceTaskCreate is None:
        return (False, None)

    due = _latest_due_date(system, company)

    payload = ComplianceTaskCreate(
        company_id=system.company_id,
        ai_system_id=system.id,
        title="Complete Fundamental Rights Impact Assessment (FRIA)",
        status="open",
        severity="high",
        mandatory=True,
        owner_user_id=getattr(system, "owner_user_id", None),
        due_date=due,
        reference="FRIA",
        reminder_days_before=30,
        notes="This task tracks the FRIA requirement.",
    )

    obj = crud_create_task(db, payload, user_id=created_by_user_id)
    return (True, int(getattr(obj, "id", 0) or 0))


def _doc_meta_dict(doc: Document) -> Dict[str, Any]:
    try:
        from json import loads

        return loads(getattr(doc, "metadata_json", "{}") or "{}")
    except Exception:
        return {}


def _save_doc_meta(db: Session, doc: Document, meta: Dict[str, Any]) -> None:
    from json import dumps

    doc.metadata_json = dumps(meta, ensure_ascii=False, separators=(",", ":"))
    db.add(doc)
    db.commit()
    db.refresh(doc)


def acknowledge_fria(
    db: Session, *, system_id: int, user_id: Optional[int]
) -> Dict[str, Any]:
    """
    Mark latest FRIA doc as acknowledged by AR.
    Stores ar_acknowledged, ar_ack_at, ar_ack_by in metadata_json.
    """
    doc = latest_fria_document(db, system_id)
    if not doc:
        raise ValueError("FRIA document not found")

    meta = _doc_meta_dict(doc)
    meta["ar_acknowledged"] = True
    meta["ar_ack_at"] = datetime.utcnow().isoformat() + "Z"
    meta["ar_ack_by"] = user_id

    _save_doc_meta(db, doc, meta)

    # Audit best-effort
    try:
        audit_log(
            db,
            company_id=doc.company_id,
            user_id=user_id,
            action="FRIA_ACKNOWLEDGED",
            entity_type="document",
            entity_id=int(doc.id),
            meta={"ai_system_id": int(doc.ai_system_id) if doc.ai_system_id else None},
            ip=None,
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "document_id": int(doc.id)}


def update_fria_metadata(
    db: Session,
    *,
    system_id: int,
    user_id: Optional[int],
    version: Optional[str] = None,
    key_risks: Optional[List[str] | str] = None,
    mitigations: Optional[str] = None,
    ar_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Patch metadata on latest FRIA document (no migration needed).
    """
    doc = latest_fria_document(db, system_id)
    if not doc:
        raise ValueError("FRIA document not found")

    meta = _doc_meta_dict(doc)
    if version is not None:
        meta["version"] = version
    if key_risks is not None:
        if isinstance(key_risks, list):
            meta["key_risks"] = key_risks
        else:
            # split by newline or ';' for convenience
            parts = [
                p.strip()
                for p in str(key_risks).replace("\r", "").replace(";", "\n").split("\n")
                if p.strip()
            ]
            meta["key_risks"] = parts
    if mitigations is not None:
        meta["mitigations"] = mitigations
    if ar_notes is not None:
        meta["ar_notes"] = ar_notes

    _save_doc_meta(db, doc, meta)

    # Audit best-effort
    try:
        audit_log(
            db,
            company_id=doc.company_id,
            user_id=user_id,
            action="FRIA_METADATA_UPDATED",
            entity_type="document",
            entity_id=int(doc.id),
            meta={
                "fields": [
                    k
                    for k in ["version", "key_risks", "mitigations", "ar_notes"]
                    if k in meta
                ]
            },
            ip=None,
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "document_id": int(doc.id), "metadata": meta}


def send_fria_to_regulator(
    db: Session, *, system_id: int, user_id: Optional[int]
) -> Dict[str, Any]:
    """
    Placeholder 'send' action – audits the intent. Integration can be added later (email/API).
    """
    system = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not system:
        raise ValueError("AI system not found")

    # Audit best-effort
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=user_id,
            action="DOC_SENT",
            entity_type="ai_system",
            entity_id=int(system.id),
            meta={
                "doc_kind": "FRIA",
                "note": "FRIA package sent to regulator (placeholder)",
            },
            ip=None,
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"ok": True, "sent": True}


def fria_status(db: Session, *, system_id: int) -> Dict[str, Any]:
    """
    Summarize FRIA dashboard status for an AI system.
    """
    system: AISystem | None = (
        db.query(AISystem).filter(AISystem.id == system_id).first()
    )
    if not system:
        raise ValueError("AI system not found")
    company: Company | None = (
        db.query(Company).filter(Company.id == system.company_id).first()
    )

    required = fria_is_required(system)

    docs = fria_documents(db, system_id)
    has_doc = len(docs) > 0
    latest_doc = docs[0] if has_doc else None
    latest_meta = _doc_meta_dict(latest_doc) if latest_doc else {}

    task = fria_task_existing(db, system_id)
    has_task = task is not None
    task_done = (getattr(task, "status", "") or "").lower() == "done" if task else False

    due_dt = (
        getattr(task, "due_date", None)
        if task is not None
        else _latest_due_date(system, company)
    )
    overdue = False
    if due_dt:
        try:
            now = datetime.utcnow()
            overdue = (
                (getattr(task, "status", "open") or "open").lower()
                not in {"done", "cancelled"}
            ) and (due_dt < now)
        except Exception:
            overdue = False

    ar_ack = bool(latest_meta.get("ar_acknowledged", False))
    doc_status_complete = (
        (getattr(latest_doc, "status", "") or "").lower() == "complete"
        if latest_doc
        else False
    )

    # derive human status
    if has_doc and (task_done or doc_status_complete):
        status_label = "completed"
    elif has_doc or has_task:
        status_label = "in_progress"
    else:
        status_label = "not_performed"

    ready_for_supervision = bool(
        required and has_doc and ar_ack and (task_done or doc_status_complete)
    )

    return {
        "ai_system": {
            "id": system.id,
            "name": getattr(system, "name", None),
            "risk_tier": getattr(system, "risk_tier", None),
            "company_id": system.company_id,
        },
        "provider_company": {
            "id": getattr(company, "id", None),
            "name": getattr(company, "name", None),
        },
        "required_by_ai_act": required,
        "fria_date": (
            getattr(latest_doc, "created_at", None).isoformat() + "Z"
            if latest_doc and getattr(latest_doc, "created_at", None)
            else None
        ),
        "status": status_label,  # completed | in_progress | not_performed
        "document_version": latest_meta.get("version"),
        "key_risks": latest_meta.get("key_risks", []),
        "mitigations": latest_meta.get("mitigations"),
        "documents": [
            {
                "id": d.id,
                "name": d.name,
                "type": d.type,
                "status": d.status,
                "storage_url": d.storage_url,
                "created_at": getattr(d, "created_at", None),
            }
            for d in docs
        ],
        "ar_acknowledged": ar_ack,
        "ar_notes": latest_meta.get("ar_notes"),
        "due_date": due_dt.isoformat() if due_dt else None,
        "overdue": overdue,
        "ready_for_supervision": ready_for_supervision,
    }
