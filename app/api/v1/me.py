# app/api/v1/me.py
from typing import List, Optional, Any, Dict

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.system_assignment import SystemAssignment
from app.models.ai_system import AISystem
from app.crud.system_assignment import get_assignments_for_user

router = APIRouter()


# ---- Schemas (lokalno za ovaj router) ---------------------------------------

class MeOut(BaseModel):
    id: int
    email: str
    role: str
    company_id: Optional[int] = None


class MeAssignmentOut(BaseModel):
    id: int
    user_id: int
    ai_system_id: int
    created_at: Any
    # opcionalno proširenje s kratkim opisom sustava
    system: Optional[Dict[str, Any]] = None


# ---- Endpoints ---------------------------------------------------------------

@router.get("/me", response_model=MeOut)
def get_me(
    current_user: User = Depends(get_current_user),
):
    return MeOut(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        company_id=current_user.company_id,
    )


@router.get("/me/assignments", response_model=List[MeAssignmentOut])
def list_my_assignments(
    include_system: bool = Query(False, description="Ako je true, vraća i sažetak AI sustava."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Vraća sve asignacije za trenutno prijavljenog korisnika.
    - Contributor: popis sustava na kojima je dodijeljen.
    - Admin/Staff/Super: vratit će prazno (osim ako su i sami contributor u nekom sustavu).
    """
    rows = get_assignments_for_user(db, current_user.id)

    out: List[MeAssignmentOut] = []
    if not include_system:
        for r in rows:
            out.append(MeAssignmentOut(
                id=r.id,
                user_id=r.user_id,
                ai_system_id=r.ai_system_id,
                created_at=r.created_at,
            ))
        return out

    # include_system = True → pridruži osnovne info o sustavu
    # (jedan upit za sve sustave)
    system_ids = [r.ai_system_id for r in rows]
    systems_map: Dict[int, AISystem] = {}
    if system_ids:
        systems = (
            db.query(AISystem)
            .filter(AISystem.id.in_(system_ids))
            .all()
        )
        systems_map = {s.id: s for s in systems}

    for r in rows:
        s = systems_map.get(r.ai_system_id)
        system_summary = None
        if s:
            system_summary = {
                "id": s.id,
                "name": s.name,
                "company_id": s.company_id,
                "status": s.status,
                "lifecycle_stage": s.lifecycle_stage,
                "risk_tier": s.risk_tier,
                "updated_at": s.updated_at,
            }
        out.append(MeAssignmentOut(
            id=r.id,
            user_id=r.user_id,
            ai_system_id=r.ai_system_id,
            created_at=r.created_at,
            system=system_summary,
        ))
    return out