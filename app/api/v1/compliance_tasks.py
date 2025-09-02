# app/api/v1/compliance_tasks.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    can_read_system, can_write_system_full, can_write_system_limited
)
from app.models.user import User
from app.schemas.compliance_task import (
    ComplianceTaskCreate, ComplianceTaskUpdate, ComplianceTaskOut
)
from app.crud.ai_system import get_system as crud_get_system
from app.crud.compliance_task import (
    get_task as crud_get_task,
    list_tasks_by_system as crud_list_tasks_by_system,
    create_task as crud_create_task,
    update_task as crud_update_task,
    delete_task as crud_delete_task,
)

router = APIRouter()

CONTRIBUTOR_ALLOWED = {
    "status",
    "evidence_url",
    "notes",
    "completed_at",
    "owner_user_id",
    "due_date",
    "reminder_days_before",
}

def _to_out(x) -> ComplianceTaskOut:
    return ComplianceTaskOut.model_validate(x)

@router.get("/ai-systems/{system_id}/tasks", response_model=List[ComplianceTaskOut])
def list_tasks(
    system_id: int,
    status_f: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    owner_user_id: Optional[int] = Query(None),         # NOVO
    reference: Optional[str] = Query(None),             # NOVO (partial match)
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("due_date"),
    order: str = Query("asc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = crud_get_system(db, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not can_read_system(db, current_user, system):
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = crud_list_tasks_by_system(
        db,
        system_id,
        status=status_f,
        severity=severity,
        owner_user_id=owner_user_id,   # NOVO
        reference=reference,           # NOVO
        skip=skip,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )
    return [_to_out(r) for r in rows]

@router.post("/ai-systems/{system_id}/tasks", response_model=ComplianceTaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    system_id: int,
    payload: ComplianceTaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = crud_get_system(db, system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not can_write_system_full(db, current_user, system):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    payload.company_id = system.company_id
    payload.ai_system_id = system.id

    obj = crud_create_task(db, payload, user_id=current_user.id)
    return _to_out(obj)

@router.put("/tasks/{task_id}", response_model=ComplianceTaskOut)
def update_task(
    task_id: int,
    payload: ComplianceTaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_task(db, task_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Task not found")

    system = crud_get_system(db, obj.ai_system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_write_system_limited(db, current_user, system):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    if not can_write_system_full(db, current_user, system):
        illegal = set(payload.model_dump(exclude_none=True).keys()) - CONTRIBUTOR_ALLOWED
        if illegal:
            raise HTTPException(
                status_code=403,
                detail=f"Contributors can only update: {', '.join(sorted(CONTRIBUTOR_ALLOWED))}",
            )

    obj = crud_update_task(db, obj, payload, user_id=current_user.id)
    return _to_out(obj)

@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    obj = crud_get_task(db, task_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Task not found")

    system = crud_get_system(db, obj.ai_system_id)
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")
    if not can_write_system_full(db, current_user, system):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    crud_delete_task(db, obj)
    return None
