# app/api/v1/assessments.py
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import can_read_company, can_write_company
from app.models.user import User
from app.models.ai_system import AISystem

from app.schemas.ai_assessment import (
    AIAssessmentCreate,
    AIAssessmentOut,
    AIAssessmentListItem,
    AIAssessmentDiff,
)

from app.crud.ai_assessment import (
    get_latest_for_system,
    list_versions_for_system,
    get_version,
    upsert_version_for_system,
    to_out,
)

router = APIRouter()


def _load_system_or_404(db: Session, system_id: int) -> AISystem:
    obj = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")
    return obj


def _answers_dict(row) -> Dict[str, Any]:
    try:
        import json
        return json.loads(row.answers_json or "{}") if getattr(row, "answers_json", None) else {}
    except Exception:
        return {}


@router.get("/ai-systems/{system_id}/assessment", response_model=AIAssessmentOut)
def get_latest_assessment(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _load_system_or_404(db, system_id)
    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    row = get_latest_for_system(db, system.id)
    if not row:
        raise HTTPException(status_code=404, detail="Assessment not found")

    return to_out(row)


@router.get("/ai-systems/{system_id}/assessments", response_model=List[AIAssessmentListItem])
def list_assessments(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("created_at", pattern="^(created_at|id)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """
    Lista svih verzija procjene (paginirano, sortiranje).
    - sort_by: created_at | id
    - order: asc | desc
    """
    system = _load_system_or_404(db, system_id)
    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = list_versions_for_system(
        db=db,
        system_id=system.id,
        skip=skip,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )
    # AIAssessmentListItem je “light” – direktno iz ORM-a je ok (from_attributes=True)
    return [
    AIAssessmentListItem.model_validate(
        {
            "id": r.id,
            "system_id": r.ai_system_id,
            "risk_tier": r.risk_tier,
            "version_tag": getattr(r, "version_tag", None),   # tolerantno
            "created_by": int(r.created_by) if r.created_by is not None else 0,
            "created_at": r.created_at,
        }
    )
    for r in rows
]


@router.get("/ai-systems/{system_id}/assessments/{assessment_id}", response_model=AIAssessmentOut)
def get_assessment_version(
    system_id: int,
    assessment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _load_system_or_404(db, system_id)
    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    # get_version očekuje (db, system_id, version_id)
    row = get_version(db, system.id, assessment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Assessment not found")

    return to_out(row)


@router.get("/ai-systems/{system_id}/assessments/{base_id}/diff/{compare_id}", response_model=AIAssessmentDiff)
def diff_assessments(
    system_id: int,
    base_id: int,
    compare_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lightweight JSON diff između dvije verzije (answers + promjena risk_tier/version_tag).
    """
    system = _load_system_or_404(db, system_id)
    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    base = get_version(db, system.id, base_id)
    compare = get_version(db, system.id, compare_id)
    if not base or not compare:
        raise HTTPException(status_code=404, detail="Assessment version not found")

    a = _answers_dict(base)
    b = _answers_dict(compare)

    # Diff
    added: Dict[str, Any] = {}
    removed: Dict[str, Any] = {}
    changed: Dict[str, Dict[str, Any]] = {}

    a_keys = set(a.keys())
    b_keys = set(b.keys())

    for k in sorted(b_keys - a_keys):
        added[k] = b[k]
    for k in sorted(a_keys - b_keys):
        removed[k] = a[k]
    for k in sorted(a_keys & b_keys):
        if a[k] != b[k]:
            changed[k] = {"from": a[k], "to": b[k]}

    return AIAssessmentDiff(
        base_id=base.id,
        compare_id=compare.id,
        risk_tier_from=getattr(base, "risk_tier", None),
        risk_tier_to=getattr(compare, "risk_tier", None),
        version_tag_from=getattr(base, "version_tag", None),
        version_tag_to=getattr(compare, "version_tag", None),
        added=added,
        removed=removed,
        changed=changed,
        summary={
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
    )


@router.post("/ai-systems/{system_id}/assessment/save", response_model=AIAssessmentOut, status_code=status.HTTP_201_CREATED)
def create_or_update_assessment(
    system_id: int,
    payload: AIAssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Kreiraj novu verziju procjene i spremi je (versioned).
    """
    system = _load_system_or_404(db, system_id)
    if not can_write_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    row = upsert_version_for_system(
        db=db,
        system=system,
        payload=payload,
        created_by=current_user.id,
    )
    return to_out(row)