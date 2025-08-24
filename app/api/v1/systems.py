from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import scope_query_to_user_company, ensure_resource_company_or_super, require_admin_in_company
from app.models.user import User
from app.models.system import System  # pretpostavljeni model

router = APIRouter()

@router.get("/systems")
def list_systems(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(System)
    q = scope_query_to_user_company(q, current_user, System.company_id)
    return [s.to_dict() for s in q.all()]

@router.get("/systems/{system_id}")
def get_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    s = db.query(System).get(system_id)
    if not s:
        raise HTTPException(404, "Not found")
    ensure_resource_company_or_super(s.company_id, current_user)
    return s.to_dict()

@router.post("/systems")
def create_system(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_admin_in_company),
):
    # force company_id to current user's company unless super
    company_id = payload.get("company_id") or current_user.company_id
    if not company_id:
        raise HTTPException(400, "company_id missing")
    if company_id != current_user.company_id and current_user.role != "super_admin":
        raise HTTPException(403, "Cannot create for another company")

    s = System(**{**payload, "company_id": company_id})
    db.add(s)
    db.commit()
    db.refresh(s)
    return s.to_dict()
