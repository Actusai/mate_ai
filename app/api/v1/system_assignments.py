# app/api/v1/system_assignments.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super, is_staff_admin, is_client_admin, is_contributor,
    can_write_system_full, can_read_system,
)
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.user import User as UserModel
from app.crud.system_assignment import (
    get_assignment, get_assignments_for_system, get_assignments_for_user,
    create_assignment, delete_assignment,
)
from app.schemas.system_assignment import SystemAssignmentOut

router = APIRouter()

def _get_system(db: Session, system_id: int) -> AISystem | None:
    return db.query(AISystem).filter(AISystem.id == system_id).first()

def _get_user(db: Session, user_id: int) -> UserModel | None:
    return db.query(UserModel).filter(UserModel.id == user_id).first()

def _can_manage_assignments(db: Session, actor: User, system: AISystem) -> bool:
    """Super, client admin (own company), staff admin (assigned company)."""
    return can_write_system_full(db, actor, system)

@router.get("/ai-systems/{system_id}/assignments", response_model=List[SystemAssignmentOut])
def list_system_assignments(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _get_system(db, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not can_read_system(db, current_user, system):
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = get_assignments_for_system(db, system_id)
    return [SystemAssignmentOut.model_validate(r) for r in rows]

@router.post("/ai-systems/{system_id}/assignments/{user_id}", response_model=SystemAssignmentOut, status_code=status.HTTP_201_CREATED)
def assign_contributor(
    system_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _get_system(db, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not _can_manage_assignments(db, current_user, system):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    target = _get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # only contributors can be assigned; must belong to same company as system
    if not is_contributor(target):
        raise HTTPException(status_code=400, detail="Only contributor/member users can be assigned to a system")
    if target.company_id != system.company_id:
        raise HTTPException(status_code=400, detail="Contributor must belong to the same company as the AI system")

    obj = create_assignment(db, user_id=user_id, ai_system_id=system_id)
    return SystemAssignmentOut.model_validate(obj)

@router.delete("/ai-systems/{system_id}/assignments/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_contributor(
    system_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _get_system(db, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not _can_manage_assignments(db, current_user, system):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    obj = get_assignment(db, user_id=user_id, ai_system_id=system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Assignment not found")

    delete_assignment(db, obj)
    return None