# app/api/v1/companies.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status, Response
from sqlalchemy.orm import Session

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
from app.schemas.company import CompanyCreate, CompanyUpdate, CompanyOut
from app.schemas.user import UserOut  # za members list
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

    if is_admin(current_user):  # uključuje staff admine
        assigned = (
            db.query(AdminAssignment.company_id)
            .filter(AdminAssignment.admin_id == current_user.id)
            .all()
        )
        ids.update([cid for (cid,) in assigned])

    return list(ids)


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
    super_admin: vraća sve kompanije (paginirano)
    admin/member: vraća samo vidljive kompanije (vlastitu; staff admin i dodijeljene)
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    """
    Samo super_admin može kreirati tenancy (nove klijente).
    """
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if not payload.company_type:
        raise HTTPException(status_code=422, detail="company_type is required")

    obj = crud_create_company(db, payload)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.id,
            user_id=getattr(current_user, "id", None),
            action="COMPANY_CREATED",
            entity_type="company",
            entity_id=obj.id,
            meta={"name": obj.name, "company_type": obj.company_type, "status": obj.status},
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    if not can_write_company(db, current_user, company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if not payload.company_type:
        raise HTTPException(status_code=422, detail="company_type is required")

    # Snapshot promjena za audit (prije update-a)
    changes = payload.model_dump(exclude_unset=True)

    obj = crud_update_company(db, obj, payload)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.id,
            user_id=getattr(current_user, "id", None),
            action="COMPANY_UPDATED",
            entity_type="company",
            entity_id=obj.id,
            meta={"changes": changes},
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    """
    For safety, deletion stays restricted to super_admin.
    """
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_get_company(db, company_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Company not found")

    # Meta prije brisanja (za audit)
    meta_snapshot = {
        "name": obj.name,
        "company_type": obj.company_type,
        "status": obj.status,
    }

    crud_delete_company(db, obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)