# app/api/v1/documents.py
from __future__ import annotations
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.rbac import (
    ensure_system_access_read,
    ensure_system_write_limited,
    ensure_system_write_full,
)
from app.models.user import User
from app.models.document import Document
from app.models.compliance_task import ComplianceTask
from app.models.ai_system import AISystem
from app.schemas.document import (
    DocumentCreate,
    DocumentUpdate,
    DocumentOut,
)
from app.services.audit import audit_log, ip_from_request

router = APIRouter()


# ---------------------------
# Helpers
# ---------------------------
def _to_out(d: Document) -> DocumentOut:
    return DocumentOut.model_validate(d)


def _require_task(db: Session, task_id: int) -> ComplianceTask:
    t = db.query(ComplianceTask).filter(ComplianceTask.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


def _require_system(db: Session, system_id: int) -> AISystem:
    s = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="AI system not found")
    return s


def _derive_scope_from_payload(db: Session, payload: DocumentCreate) -> Dict[str, int]:
    """
    Resolve (company_id, ai_system_id) from payload.
    - If task_id is provided, use its ai_system_id + company_id.
    - Else require ai_system_id to exist and use its company_id.
    - If both task_id and ai_system_id are provided, validate they match.
    """
    if payload.task_id:
        task = _require_task(db, payload.task_id)
        if payload.ai_system_id and payload.ai_system_id != task.ai_system_id:
            raise HTTPException(
                status_code=400,
                detail="Provided ai_system_id does not match the task's ai_system_id",
            )
        system = _require_system(db, task.ai_system_id)
        return {"company_id": system.company_id, "ai_system_id": system.id}

    # no task_id -> require ai_system_id
    if not payload.ai_system_id:
        raise HTTPException(status_code=400, detail="Either 'ai_system_id' or 'task_id' must be provided")
    system = _require_system(db, payload.ai_system_id)
    return {"company_id": system.company_id, "ai_system_id": system.id}


def _resolve_doc_scope(db: Session, doc: Document) -> Dict[str, Optional[int]]:
    """
    For RBAC checks on an existing document, resolve its owning ai_system_id and company_id.
    If ai_system_id is null but task_id is set, fetch the task to get the system.
    """
    company_id = doc.company_id
    system_id = doc.ai_system_id
    if system_id is None and doc.task_id:
        task = _require_task(db, doc.task_id)
        system_id = task.ai_system_id
    return {"company_id": company_id, "ai_system_id": system_id}


# ---------------------------
# Create / Read single
# ---------------------------
@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a document/evidence.
    - Requires at least limited write access to the underlying AI system.
    - company_id is derived from system/task; client cannot override it.
    """
    scope = _derive_scope_from_payload(db, payload)

    # RBAC: limited write on the resolved system
    ensure_system_write_limited(db, current_user, scope["ai_system_id"])

    obj = Document(
        company_id=scope["company_id"],
        ai_system_id=scope["ai_system_id"],
        task_id=payload.task_id,
        document_type=payload.document_type,
        version=payload.version,
        effective_date=payload.effective_date,
        url=payload.url,
        uploaded_by=getattr(current_user, "id", None),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=getattr(current_user, "id", None),
            action="DOCUMENT_CREATED",
            entity_type="document",
            entity_id=obj.id,
            meta={
                "document_type": obj.document_type,
                "ai_system_id": obj.ai_system_id,
                "task_id": obj.task_id,
                "url": obj.url,
                "version": obj.version,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch a single document.
    - Requires read access to the underlying AI system.
    """
    obj = db.query(Document).filter(Document.id == document_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Document not found")

    scope = _resolve_doc_scope(db, obj)
    if scope["ai_system_id"] is None:
        # Highly unlikely (orphaned) but ensure visibility by company via any system? Keep strict and deny.
        raise HTTPException(status_code=400, detail="Document is orphaned (no AI system link)")

    ensure_system_access_read(db, current_user, scope["ai_system_id"])
    return _to_out(obj)


# ---------------------------
# Lists by system / task
# ---------------------------
@router.get("/ai-systems/{system_id}/documents", response_model=List[DocumentOut])
def list_documents_for_system(
    system_id: int,
    document_type: Optional[str] = Query(None, description="Optional filter by document_type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List documents linked to a specific AI system.
    - Requires read access to the system.
    """
    _ = ensure_system_access_read(db, current_user, system_id)

    q = db.query(Document).filter(Document.ai_system_id == system_id)
    if document_type:
        q = q.filter(Document.document_type == document_type.strip())

    rows = (
        q.order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/tasks/{task_id}/documents", response_model=List[DocumentOut])
def list_documents_for_task(
    task_id: int,
    document_type: Optional[str] = Query(None, description="Optional filter by document_type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List documents linked to a specific compliance task (evidence).
    - Requires read access to the task's AI system.
    """
    task = _require_task(db, task_id)
    _ = ensure_system_access_read(db, current_user, task.ai_system_id)

    q = db.query(Document).filter(Document.task_id == task_id)
    if document_type:
        q = q.filter(Document.document_type == document_type.strip())

    rows = (
        q.order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_to_out(r) for r in rows]


# ---------------------------
# Update / Delete
# ---------------------------
@router.put("/documents/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update document metadata (type, version, effective_date, url).
    - Requires at least limited write access to the underlying AI system.
    - Re-linking to another system/task is not supported here.
    """
    obj = db.query(Document).filter(Document.id == document_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Document not found")

    scope = _resolve_doc_scope(db, obj)
    if scope["ai_system_id"] is None:
        raise HTTPException(status_code=400, detail="Document is orphaned (no AI system link)")

    ensure_system_write_limited(db, current_user, scope["ai_system_id"])

    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(obj, k, v)

    db.add(obj)
    db.commit()
    db.refresh(obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=getattr(current_user, "id", None),
            action="DOCUMENT_UPDATED",
            entity_type="document",
            entity_id=obj.id,
            meta={"changes": data},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Delete a document.
    - Requires full write access to the underlying AI system.
    """
    obj = db.query(Document).filter(Document.id == document_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Document not found")

    scope = _resolve_doc_scope(db, obj)
    if scope["ai_system_id"] is None:
        raise HTTPException(status_code=400, detail="Document is orphaned (no AI system link)")

    # Stricter permission for destructive action
    ensure_system_write_full(db, current_user, scope["ai_system_id"])

    meta_snapshot = {
        "document_type": obj.document_type,
        "ai_system_id": obj.ai_system_id,
        "task_id": obj.task_id,
        "url": obj.url,
        "version": obj.version,
    }

    db.delete(obj)
    db.commit()

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=scope["company_id"],
            user_id=getattr(current_user, "id", None),
            action="DOCUMENT_DELETED",
            entity_type="document",
            entity_id=document_id,
            meta=meta_snapshot,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)