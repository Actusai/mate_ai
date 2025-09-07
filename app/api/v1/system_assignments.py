# app/api/v1/system_assignments.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.user import User as UserModel
from app.core.rbac import (
    ensure_system_access_read,
    ensure_system_write_full,
)
from app.crud.system_assignment import (
    get_assignment,
    get_assignments_for_system,
    get_assignments_for_user,
    create_assignment,
    delete_assignment,
    # enriched helpers:
    get_assignments_with_user_for_system,
    get_assignment_with_user,
)
from app.schemas.system_assignment import (
    SystemAssignmentOut,
    SystemAssignmentDetailedOut,
    UserMini,
)
from app.services.audit import audit_log, ip_from_request

router = APIRouter()

# ----------------------------
# Helpers
# ----------------------------
def _get_user(db: Session, user_id: int) -> UserModel | None:
    return db.query(UserModel).filter(UserModel.id == user_id).first()

# ----------------------------
# LIST (enriched)
# ----------------------------
@router.get("/ai-systems/{system_id}/assignments", response_model=List[SystemAssignmentDetailedOut])
def list_system_assignments(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # RBAC: mora imati read access na sustav
    system: AISystem = ensure_system_access_read(db, current_user, system_id)

    rows = get_assignments_with_user_for_system(db, system_id)
    out: List[SystemAssignmentDetailedOut] = []
    for assignment, usr in rows:
        out.append(
            SystemAssignmentDetailedOut(
                id=assignment.id,
                user_id=assignment.user_id,
                ai_system_id=assignment.ai_system_id,
                created_at=assignment.created_at,
                user=UserMini(
                    id=usr.id,
                    email=usr.email,
                    role=(usr.role or ""),
                    full_name=getattr(usr, "full_name", None),
                ),
            )
        )
    return out

# ----------------------------
# CREATE (enriched)
# ----------------------------
@router.post(
    "/ai-systems/{system_id}/assignments/{user_id}",
    response_model=SystemAssignmentDetailedOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_contributor(
    system_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    # RBAC: treba full write na sustav
    system: AISystem = ensure_system_write_full(db, current_user, system_id)

    target = _get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # samo contributor/member; i mora biti iz iste company kao sustav
    role_lower = (target.role or "").lower()
    if role_lower not in {"member", "contributor"}:
        raise HTTPException(status_code=400, detail="Only contributor/member users can be assigned to a system")
    if target.company_id != system.company_id:
        raise HTTPException(status_code=400, detail="User must belong to the same company as the AI system")

    create_assignment(db, user_id=user_id, ai_system_id=system_id)

    row = get_assignment_with_user(db, user_id=user_id, ai_system_id=system_id)
    if not row:
        raise HTTPException(status_code=500, detail="Assignment created, but could not be reloaded")
    assignment, usr = row

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_ASSIGNMENT_CREATED",
            entity_type="system_assignment",
            entity_id=getattr(assignment, "id", None),
            meta={"ai_system_id": system_id, "target_user_id": user_id},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return SystemAssignmentDetailedOut(
        id=assignment.id,
        user_id=assignment.user_id,
        ai_system_id=assignment.ai_system_id,
        created_at=assignment.created_at,
        user=UserMini(
            id=usr.id,
            email=usr.email,
            role=(usr.role or ""),
            full_name=getattr(usr, "full_name", None),
        ),
    )

# ----------------------------
# DELETE
# ----------------------------
@router.delete("/ai-systems/{system_id}/assignments/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_contributor(
    system_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
) -> Response:
    # RBAC: treba full write na sustav
    system: AISystem = ensure_system_write_full(db, current_user, system_id)

    obj = get_assignment(db, user_id=user_id, ai_system_id=system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Assignment not found")

    delete_assignment(db, obj)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_ASSIGNMENT_DELETED",
            entity_type="system_assignment",
            entity_id=getattr(obj, "id", None),
            meta={"ai_system_id": system_id, "target_user_id": user_id},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)