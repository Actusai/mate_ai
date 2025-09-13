# app/api/v1/companies.py
from __future__ import annotations

from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super,
    is_admin,
    can_read_company,
    can_write_company,
)
from app.models.user import User
from app.models.company import Company
from app.models.admin_assignment import AdminAssignment
from app.models.document import Document  # NEW: used for AR appointment status
from app.schemas.company import CompanyCreate, CompanyUpdate, CompanyOut
from app.crud.company import (
    get_company as crud_get_company,
    create_company as crud_create_company,
    update_company as crud_update_company,
    delete_company as crud_delete_company,
)
from app.services.audit import audit_log, ip_from_request  # AUDIT

router = APIRouter()


def _to_out(c: Company) -> CompanyOut:
    return CompanyOut.model_validate(c)


def _visible_company_ids_for_user(db: Session, current_user: User) -> list[int]:
    """
    Visible IDs for non-super users:
      - member: own company only
      - client admin: own company only
      - staff admin: own + assigned
    Super admin is handled separately (no filter).
    """
    ids = set()
    if current_user.company_id:
        ids.add(current_user.company_id)

    if is_admin(current_user):  # includes staff admins
        assigned = (
            db.query(AdminAssignment.company_id)
            .filter(AdminAssignment.admin_id == current_user.id)
            .all()
        )
        ids.update([cid for (cid,) in assigned])

    return list(ids)


# ---------- AR helpers (non-breaking) ----------
_AR_COMPANY_TYPES = {"authorized_representative", "ar"}
_AR_DOC_TYPES = {"ar_appointment", "ar_mandate", "ar_letter"}


def _is_ar_company(c: Company) -> bool:
    ct = (getattr(c, "company_type", None) or "").strip().lower().replace("-", "_")
    return ct in _AR_COMPANY_TYPES


def _latest_ar_doc(db: Session, company_id: int) -> Optional[Document]:
    """
    Company-level AR appointment doc is stored with:
      - documents.company_id = company_id
      - documents.ai_system_id IS NULL
      - documents.type in _AR_DOC_TYPES
    """
    return (
        db.query(Document)
        .filter(
            Document.company_id == company_id,
            Document.ai_system_id.is_(None),
            Document.type.in_(list(_AR_DOC_TYPES)),
        )
        .order_by(Document.created_at.desc(), Document.id.desc())
        .first()
    )


@router.get(
    "/companies",
    response_model=List[CompanyOut],
    operation_id="companies_list_v1",
)
def list_companies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Super admin: returns all companies (paginated).
    Admin/member: returns only visible companies (own; staff admins also assigned).
    """
    if is_super(current_user):
        rows = (
            db.query(Company)
            .order_by(Company.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return [_to_out(r) for r in rows]

    visible_ids = _visible_company_ids_for_user(db, current_user)
    if not visible_ids:
        return []

    rows = (
        db.query(Company)
        .filter(Company.id.in_(visible_ids))
        .order_by(Company.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post(
    "/companies",
    response_model=CompanyOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="companies_create_v1",
)
def create_company_endpoint(
    payload: CompanyCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Only super_admin can create tenancy (new clients).
    """
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if not payload.company_type:
        raise HTTPException(status_code=422, detail="company_type is required")

    obj = crud_create_company(db, payload)

    # AUDIT (best-effort)
    try:
        meta: Dict[str, Any] = {
            "name": obj.name,
            "company_type": obj.company_type,
            "status": obj.status,
        }
        # Soft hint for AR companies: appointment doc will be required
        if _is_ar_company(obj):
            meta["hint"] = (
                "AR company created. Expect an AR appointment document (type='ar_appointment')."
            )
        audit_log(
            db,
            company_id=obj.id,
            user_id=getattr(current_user, "id", None),
            action="COMPANY_CREATED",
            entity_type="company",
            entity_id=obj.id,
            meta=meta,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.get(
    "/companies/{company_id}",
    response_model=CompanyOut,
    operation_id="companies_get_v1",
)
def get_company_endpoint(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    if not can_read_company(db, current_user, company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    return _to_out(obj)


@router.put(
    "/companies/{company_id}",
    response_model=CompanyOut,
    operation_id="companies_update_v1",
)
def update_company_endpoint(
    company_id: int,
    payload: CompanyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    if not can_write_company(db, current_user, company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if not payload.company_type:
        raise HTTPException(status_code=422, detail="company_type is required")

    # Snapshot of requested changes for audit (before update)
    changes = payload.model_dump(exclude_unset=True)

    obj = crud_update_company(db, obj, payload)

    # AUDIT (best-effort) + soft AR hint if applicable
    try:
        meta: Dict[str, Any] = {"changes": changes}
        if _is_ar_company(obj):
            ar_doc = _latest_ar_doc(db, obj.id)
            meta["ar_requirement"] = {
                "required": True,
                "has_document": bool(ar_doc),
                "document_id": getattr(ar_doc, "id", None) if ar_doc else None,
                "document_status": getattr(ar_doc, "status", None) if ar_doc else None,
            }
            if not ar_doc:
                meta["hint"] = (
                    "Missing AR appointment document (type='ar_appointment')."
                )
        audit_log(
            db,
            company_id=obj.id,
            user_id=getattr(current_user, "id", None),
            action="COMPANY_UPDATED",
            entity_type="company",
            entity_id=obj.id,
            meta=meta,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.delete(
    "/companies/{company_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="companies_delete_v1",
)
def delete_company_endpoint(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    For safety, deletion stays restricted to super_admin.
    """
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    # Snapshot before deletion (for audit)
    meta_snapshot = {
        "name": obj.name,
        "company_type": obj.company_type,
        "status": obj.status,
    }

    # Perform delete
    crud_delete_company(db, obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=company_id,
            user_id=getattr(current_user, "id", None),
            action="COMPANY_DELETED",
            entity_type="company",
            entity_id=company_id,
            meta=meta_snapshot,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- NEW: AR appointment status endpoint (additive, non-breaking) ----------
@router.get(
    "/companies/{company_id}/ar-status",
    operation_id="companies_ar_status_v1",
)
def company_ar_status(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Returns AR appointment requirement status for the company:
      {
        "company_id": ...,
        "is_ar_company": true|false,
        "required": true|false,
        "has_document": true|false,
        "document_id": int|null,
        "document_status": str|null,
        "hints": [ ... ]
      }
    """
    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    if not can_read_company(db, current_user, company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    is_ar = _is_ar_company(obj)
    if not is_ar:
        return {
            "company_id": company_id,
            "is_ar_company": False,
            "required": False,
            "has_document": False,
            "document_id": None,
            "document_status": None,
            "hints": [],
        }

    doc = _latest_ar_doc(db, company_id)
    hints: List[str] = []
    if not doc:
        hints.append(
            "Upload AR appointment (type='ar_appointment') at company scope (no AI system)."
        )

    return {
        "company_id": company_id,
        "is_ar_company": True,
        "required": True,
        "has_document": bool(doc),
        "document_id": getattr(doc, "id", None) if doc else None,
        "document_status": getattr(doc, "status", None) if doc else None,
        "hints": hints,
    }
