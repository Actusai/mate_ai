# app/api/v1/admin_assignments.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.schemas.admin_assignment import (
    AdminAssignmentCreate,
    AdminAssignmentOut,
)
from app.crud import admin_assignment as crud


router = APIRouter()


def _require_super(user: User):
    if (user.role or "").lower() != "super_admin":
        raise HTTPException(status_code=403, detail="Only super_admin allowed")


@router.get("/admin-assignments", response_model=List[AdminAssignmentOut])
def list_assignments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super(current_user)
    rows = crud.list_all(db)
    return [AdminAssignmentOut.model_validate(r) for r in rows]


@router.get(
    "/admin-assignments/by-company/{company_id}",
    response_model=List[AdminAssignmentOut],
)
def list_assignments_by_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super(current_user)
    rows = crud.list_by_company(db, company_id)
    return [AdminAssignmentOut.model_validate(r) for r in rows]


@router.get(
    "/admin-assignments/by-admin/{admin_user_id}",
    response_model=List[AdminAssignmentOut],
)
def list_assignments_by_admin(
    admin_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super(current_user)
    rows = crud.list_by_admin(db, admin_user_id)
    return [AdminAssignmentOut.model_validate(r) for r in rows]


@router.post(
    "/admin-assignments",
    response_model=AdminAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    payload: AdminAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super(current_user)
    try:
        obj = crud.create(
            db, admin_user_id=payload.admin_user_id, company_id=payload.company_id
        )
        return AdminAssignmentOut.model_validate(obj)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/admin-assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super(current_user)
    obj = crud.get(db, assignment_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Assignment not found")
    crud.delete(db, obj)
    return
