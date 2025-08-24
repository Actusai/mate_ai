# app/api/v1/systems.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super,
    is_admin,
    can_read_company,
    can_write_company,
    get_assigned_company_ids,
)
from app.models.user import User
from app.models.ai_system import AISystem
from app.schemas.ai_system import AISystemCreate, AISystemUpdate, AISystemOut
from app.crud.ai_system import (
    get_system as crud_get_system,
    get_all_systems as crud_get_all_systems,
    get_systems_by_company_ids as crud_get_systems_by_company_ids,
    create_system as crud_create_system,
    update_system as crud_update_system,
    delete_system as crud_delete_system,
)

router = APIRouter()


def _to_out(s: AISystem) -> AISystemOut:
    return AISystemOut.model_validate(s)


def _visible_company_ids_for_user(db: Session, current_user: User) -> list[int]:
    """
    Visibility for non-super users:
      - member (client): only own company_id
      - client admin: only own company_id
      - staff admin (administrator_stranice/site_admin): own company_id (if any) + assigned companies
    """
    ids = set()
    if current_user.company_id:
        ids.add(current_user.company_id)

    if is_admin(current_user):
        assigned = get_assigned_company_ids(db, current_user.id)
        ids.update(assigned)

    return list(ids)


@router.get("/ai-systems", response_model=List[AISystemOut])
def list_ai_systems(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    super_admin: all systems (paginated)
    admin/member: systems for visible companies (own; staff admins also assigned)
    """
    if is_super(current_user):
        rows = crud_get_all_systems(db, skip=skip, limit=limit)
        return [_to_out(r) for r in rows]

    visible_ids = _visible_company_ids_for_user(db, current_user)
    if not visible_ids:
        return []

    rows = crud_get_systems_by_company_ids(db, visible_ids, skip=skip, limit=limit)
    return [_to_out(r) for r in rows]


@router.post("/ai-systems", response_model=AISystemOut, status_code=status.HTTP_201_CREATED)
def create_ai_system(
    payload: AISystemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create AI system for a company.
    Permissions:
      - super_admin: can create for any company
      - client admin: can create for their own company_id
      - staff admin: can create only for companies they are assigned to
      - member: cannot create
    """
    if not can_write_company(db, current_user, payload.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    try:
        obj = crud_create_system(db, payload)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    return _to_out(obj)


@router.get("/ai-systems/{system_id}", response_model=AISystemOut)
def get_ai_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_read_company(db, current_user, obj.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    return _to_out(obj)


@router.put("/ai-systems/{system_id}", response_model=AISystemOut)
def update_ai_system(
    system_id: int,
    payload: AISystemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_write_company(db, current_user, obj.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = crud_update_system(db, obj, payload)
    return _to_out(obj)


@router.delete("/ai-systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_write_company(db, current_user, obj.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    crud_delete_system(db, obj)
    return None