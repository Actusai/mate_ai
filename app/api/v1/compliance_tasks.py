# app/api/v1/compliance_tasks.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
# RBAC helpers
from app.core.rbac import (
    ensure_system_access_read,
    ensure_system_write_limited,
    ensure_system_write_full,
)

from app.models.user import User
from app.schemas.compliance_task import (
    ComplianceTaskCreate, ComplianceTaskUpdate, ComplianceTaskOut
)
from app.crud.compliance_task import (
    get_task as crud_get_task,
    list_tasks_by_system as crud_list_tasks_by_system,
    create_task as crud_create_task,
    update_task as crud_update_task,
    delete_task as crud_delete_task,
)
from app.services.audit import audit_log, ip_from_request
from app.services.reporting import (
    compute_compliance_status_for_system,
    compute_compliance_snapshot,
)

router = APIRouter()

# Fields a contributor is allowed to update
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
    owner_user_id: Optional[int] = Query(None),         # filter by owner
    reference: Optional[str] = Query(None),             # partial match
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("due_date"),
    order: str = Query("asc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List compliance tasks for a given AI system (RBAC-scoped).
    """
    # RBAC: must have read access to the AI system
    _ = ensure_system_access_read(db, current_user, system_id)

    rows = crud_list_tasks_by_system(
        db,
        system_id,
        status=status_f,
        severity=severity,
        owner_user_id=owner_user_id,
        reference=reference,
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new compliance task.
    Emits TASK_CREATED audit and, if overall system compliance changes, COMPLIANCE_STATUS_CHANGED.
    """
    # RBAC: full write required
    system = ensure_system_write_full(db, current_user, system_id)

    # Enforce scoping fields
    payload.company_id = system.company_id
    payload.ai_system_id = system.id

    # Compute compliance snapshot BEFORE change
    old_cs = compute_compliance_status_for_system(db, system.id)
    old_snap = compute_compliance_snapshot(db, system.id)

    obj = crud_create_task(db, payload, user_id=current_user.id)

    # Compute AFTER change
    new_cs = compute_compliance_status_for_system(db, system.id)
    new_snap = compute_compliance_snapshot(db, system.id)

    # --- AUDIT (best-effort) ---
    try:
        # Business event
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="TASK_CREATED",
            entity_type="compliance_task",
            entity_id=obj.id,
            meta={
                "title": getattr(obj, "title", None),
                "ai_system_id": getattr(obj, "ai_system_id", None),
                "status": getattr(obj, "status", None),
                "due_date": getattr(obj, "due_date", None),
                "owner_user_id": getattr(obj, "owner_user_id", None),
            },
            ip=ip_from_request(request),
        )
        # Derived-state change
        if old_cs != new_cs:
            audit_log(
                db,
                company_id=system.company_id,
                user_id=current_user.id,
                action="COMPLIANCE_STATUS_CHANGED",
                entity_type="ai_system",
                entity_id=system.id,
                meta={
                    "ai_system_id": system.id,
                    "from": old_cs,
                    "to": new_cs,
                    "reason": "task_created",
                    "snapshot_before": old_snap,
                    "snapshot_after": new_snap,
                },
                ip=ip_from_request(request),
            )
        db.commit()
    except Exception:
        db.rollback()  # audits must not break business operation

    return _to_out(obj)


@router.put("/tasks/{task_id}", response_model=ComplianceTaskOut)
def update_task(
    task_id: int,
    payload: ComplianceTaskUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a compliance task (limited fields for contributors).
    Emits TASK_UPDATED audit and, if overall system compliance changes, COMPLIANCE_STATUS_CHANGED.
    """
    obj = crud_get_task(db, task_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Task not found")

    # RBAC: at least limited write on the system
    system = ensure_system_write_limited(db, current_user, obj.ai_system_id)

    # If not full, restrict fields to CONTRIBUTOR_ALLOWED
    has_full = True
    try:
        ensure_system_write_full(db, current_user, obj.ai_system_id)
    except HTTPException:
        has_full = False

    if not has_full:
        illegal = set(payload.model_dump(exclude_none=True).keys()) - CONTRIBUTOR_ALLOWED
        if illegal:
            allowed = ", ".join(sorted(CONTRIBUTOR_ALLOWED))
            raise HTTPException(
                status_code=403,
                detail=f"Contributors can only update: {allowed}",
            )

    # Task-level snapshot for audit
    old_task_status = getattr(obj, "status", None)
    changes = payload.model_dump(exclude_none=True)

    # System compliance BEFORE change
    old_cs = compute_compliance_status_for_system(db, system.id)
    old_snap = compute_compliance_snapshot(db, system.id)

    # Perform update (commits inside)
    obj = crud_update_task(db, obj, payload, user_id=current_user.id)

    # System compliance AFTER change
    new_cs = compute_compliance_status_for_system(db, system.id)
    new_snap = compute_compliance_snapshot(db, system.id)

    # --- AUDIT (best-effort) ---
    try:
        # Business event
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="TASK_UPDATED",
            entity_type="compliance_task",
            entity_id=obj.id,
            meta={
                "fields": changes,
                "old_status": old_task_status,
                "new_status": getattr(obj, "status", None),
                "ai_system_id": getattr(obj, "ai_system_id", None),
            },
            ip=ip_from_request(request),
        )
        # Derived-state change
        if old_cs != new_cs:
            audit_log(
                db,
                company_id=system.company_id,
                user_id=current_user.id,
                action="COMPLIANCE_STATUS_CHANGED",
                entity_type="ai_system",
                entity_id=system.id,
                meta={
                    "ai_system_id": system.id,
                    "from": old_cs,
                    "to": new_cs,
                    "reason": "task_updated",
                    "snapshot_before": old_snap,
                    "snapshot_after": new_snap,
                },
                ip=ip_from_request(request),
            )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Delete a task.
    Emits TASK_DELETED audit and, if overall system compliance changes, COMPLIANCE_STATUS_CHANGED.
    """
    obj = crud_get_task(db, task_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Task not found")

    # RBAC: full write required for deletion
    system = ensure_system_write_full(db, current_user, obj.ai_system_id)

    # Task-level snapshot for audit
    meta_snapshot = {
        "ai_system_id": getattr(obj, "ai_system_id", None),
        "title": getattr(obj, "title", None),
        "status": getattr(obj, "status", None),
        "owner_user_id": getattr(obj, "owner_user_id", None),
        "due_date": getattr(obj, "due_date", None),
    }

    # System compliance BEFORE change
    old_cs = compute_compliance_status_for_system(db, system.id)
    old_snap = compute_compliance_snapshot(db, system.id)

    # Perform delete (commits inside)
    crud_delete_task(db, obj)

    # System compliance AFTER change
    new_cs = compute_compliance_status_for_system(db, system.id)
    new_snap = compute_compliance_snapshot(db, system.id)

    # --- AUDIT (best-effort) ---
    try:
        # Business event
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="TASK_DELETED",
            entity_type="compliance_task",
            entity_id=task_id,
            meta=meta_snapshot,
            ip=ip_from_request(request),
        )
        # Derived-state change
        if old_cs != new_cs:
            audit_log(
                db,
                company_id=system.company_id,
                user_id=current_user.id,
                action="COMPLIANCE_STATUS_CHANGED",
                entity_type="ai_system",
                entity_id=system.id,
                meta={
                    "ai_system_id": system.id,
                    "from": old_cs,
                    "to": new_cs,
                    "reason": "task_deleted",
                    "snapshot_before": old_snap,
                    "snapshot_after": new_snap,
                },
                ip=ip_from_request(request),
            )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)