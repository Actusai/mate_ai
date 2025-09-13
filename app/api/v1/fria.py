# app/api/v1/fria.py
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_

from app.core.auth import get_db, get_current_user
from app.core.rbac import (
    ensure_system_access_read,
    ensure_system_write_limited,
)
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.document import Document
from app.services.audit import audit_log, ip_from_request
from app.services.compliance import get_fria_status, get_ar_readiness

# Optional: CRUD for creating a FRIA task
try:
    from app.schemas.compliance_task import ComplianceTaskCreate
    from app.crud.compliance_task import (
        list_tasks_by_system as crud_list_tasks_by_system,
        create_task as crud_create_task,
    )
except Exception:
    ComplianceTaskCreate = None  # type: ignore
    crud_list_tasks_by_system = None  # type: ignore
    crud_create_task = None  # type: ignore

# Optional: model to more directly search tasks
try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:
    ComplianceTask = None  # type: ignore

# Optional schema for documents
try:
    from app.schemas.document import DocumentOut
except Exception:
    DocumentOut = None  # type: ignore

router = APIRouter(prefix="/fria", tags=["fria"])

# FRIA document type aliases used in this app
_FRIA_DOC_TYPES = {"fria", "fria_report", "fria_pdf"}


def _doc_to_out(doc: Document) -> Dict[str, Any]:
    """Lightweight projection if DocumentOut schema is unavailable."""
    meta = None
    try:
        if getattr(doc, "metadata_json", None):
            meta = json.loads(doc.metadata_json or "{}")
    except Exception:
        meta = None

    base = {
        "id": doc.id,
        "company_id": doc.company_id,
        "ai_system_id": doc.ai_system_id,
        "uploaded_by": doc.uploaded_by,
        "name": doc.name,
        "storage_url": doc.storage_url,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "type": doc.type,
        "status": doc.status,
        "review_due_at": getattr(doc, "review_due_at", None),
        "created_at": getattr(doc, "created_at", None),
        "updated_at": getattr(doc, "updated_at", None),
        "metadata": meta,
    }
    if DocumentOut:
        return DocumentOut.model_validate(base).model_dump()
    return base


def _latest_fria_doc(db: Session, system: AISystem) -> Optional[Document]:
    return (
        db.query(Document)
        .filter(
            and_(
                Document.company_id == system.company_id,
                Document.ai_system_id == system.id,
                Document.type.in_(list(_FRIA_DOC_TYPES)),
            )
        )
        .order_by(Document.created_at.desc().nulls_last(), Document.id.desc())
        .first()
    )


# ---------------------------------
# Status & readiness
# ---------------------------------
@router.get("/ai-systems/{system_id}/status")
def fria_status(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Returns FRIA status snapshot for the AI system:
      { required, status: completed|in_progress|missing|not_required|unknown, document_id, document_status, ... }
    """
    _ = ensure_system_access_read(db, current_user, system_id)
    return get_fria_status(db, system_id)


@router.get("/ai-systems/{system_id}/ar-readiness")
def ar_readiness(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Returns AR readiness bundle for supervision (AI Office / market surveillance):
      - FRIA status
      - Conformity document presence
      - Technical documentation pack presence
      - Open high/critical incidents
      - ready_for_supervision + blockers + hints
    """
    _ = ensure_system_access_read(db, current_user, system_id)
    return get_ar_readiness(db, system_id)


# ---------------------------------
# Documents list
# ---------------------------------
@router.get("/ai-systems/{system_id}/documents")
def list_fria_documents(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """
    Lists FRIA-related documents for the given AI system.
    Document types recognized: 'fria', 'fria_report', 'fria_pdf'.
    """
    system: AISystem = ensure_system_access_read(db, current_user, system_id)
    rows = (
        db.query(Document)
        .filter(
            Document.company_id == system.company_id,
            Document.ai_system_id == system.id,
            Document.type.in_(list(_FRIA_DOC_TYPES)),
        )
        .order_by(Document.created_at.desc().nulls_last(), Document.id.desc())
        .all()
    )
    return [_doc_to_out(r) for r in rows]


# ---------------------------------
# Create FRIA task (if not already open)
# ---------------------------------
@router.post("/ai-systems/{system_id}/request")
def request_fria(
    system_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Creates a FRIA task if one is not already open. Intended for AR / system owner.
    Returns { created: bool, task_id: Optional[int], message }.

    Requires at least limited write on the system.
    """
    system: AISystem = ensure_system_write_limited(db, current_user, system_id)

    if (
        crud_list_tasks_by_system is None
        or crud_create_task is None
        or ComplianceTaskCreate is None
    ):
        raise HTTPException(
            status_code=400,
            detail="Compliance task APIs are not available in this deployment.",
        )

    # Check for existing open FRIA task (title/reference mention)
    existing_open = None
    if ComplianceTask is not None:
        conds = [func.lower(ComplianceTask.title).like("%fria%")]
        if hasattr(ComplianceTask, "reference"):
            conds.append(func.lower(ComplianceTask.reference).like("%fria%"))
        existing_open = (
            db.query(ComplianceTask)
            .filter(
                ComplianceTask.ai_system_id == system.id,
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                or_(*conds),
            )
            .order_by(ComplianceTask.id.desc())
            .first()
        )

    if existing_open:
        return {
            "created": False,
            "task_id": int(existing_open.id),
            "message": "An open FRIA task already exists.",
        }

    # Create a new FRIA task
    payload = ComplianceTaskCreate(
        company_id=system.company_id,
        ai_system_id=system.id,
        title="Perform Fundamental Rights Impact Assessment (FRIA)",
        status="open",
        severity="high",
        mandatory=True,
        owner_user_id=getattr(system, "owner_user_id", None),
        reference="FRIA (AI Act Art. 29)",
        reminder_days_before=30,
    )
    obj = crud_create_task(db, payload, user_id=current_user.id)

    # Best-effort audit
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="FRIA_REQUESTED",
            entity_type="compliance_task",
            entity_id=obj.id,
            meta={
                "ai_system_id": system.id,
                "title": payload.title,
                "severity": payload.severity,
                "mandatory": payload.mandatory,
                "reference": payload.reference,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return {
        "created": True,
        "task_id": int(obj.id),
        "message": "FRIA task created.",
    }


# ---------------------------------
# AR acknowledgement (also annotate FRIA doc metadata)
# ---------------------------------
from pydantic import BaseModel


class FriaAcknowledgeBody(BaseModel):
    document_id: Optional[int] = None
    note: Optional[str] = None


@router.post("/ai-systems/{system_id}/acknowledge")
def acknowledge_fria(
    system_id: int,
    body: FriaAcknowledgeBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    AR acknowledges receipt of FRIA documentation (or intent to deliver shortly).
    - Writes an audit event (FRIA_ACKNOWLEDGED)
    - If a FRIA document exists (by provided document_id or latest), annotate its metadata_json:
        ar_acknowledged, ar_acknowledged_by, ar_acknowledged_at, optional note
    """
    system: AISystem = ensure_system_write_limited(db, current_user, system_id)

    updated_doc: Optional[Document] = None
    # Try to locate target FRIA document
    if body.document_id is not None:
        candidate = (
            db.query(Document)
            .filter(
                and_(
                    Document.id == body.document_id,
                    Document.company_id == system.company_id,
                    Document.ai_system_id == system.id,
                    Document.type.in_(list(_FRIA_DOC_TYPES)),
                )
            )
            .first()
        )
        if candidate:
            updated_doc = candidate
    else:
        updated_doc = _latest_fria_doc(db, system)

    # Annotate metadata_json if we have a doc
    if updated_doc is not None:
        try:
            meta = {}
            if getattr(updated_doc, "metadata_json", None):
                meta = json.loads(updated_doc.metadata_json or "{}")
            meta.update(
                {
                    "ar_acknowledged": True,
                    "ar_acknowledged_by": getattr(current_user, "id", None),
                    "ar_acknowledged_at": datetime.utcnow().isoformat() + "Z",
                }
            )
            if body.note:
                meta["ar_acknowledged_note"] = body.note
            updated_doc.metadata_json = json.dumps(
                meta, ensure_ascii=False, separators=(",", ":")
            )
            db.add(updated_doc)
            db.commit()
            db.refresh(updated_doc)
        except Exception:
            db.rollback()

    # Best-effort audit regardless
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="FRIA_ACKNOWLEDGED",
            entity_type="ai_system",
            entity_id=system.id,
            meta={
                "ai_system_id": system.id,
                "document_id": getattr(updated_doc, "id", body.document_id),
                "note": body.note,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    if updated_doc is None:
        return {
            "ok": True,
            "message": "Acknowledgement recorded. No FRIA document found to annotate.",
        }

    return {
        "ok": True,
        "message": "Acknowledgement recorded and FRIA document annotated.",
        "document": _doc_to_out(updated_doc),
    }
