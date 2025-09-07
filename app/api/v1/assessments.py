# app/api/v1/assessments.py
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.auth import get_db, get_current_user
from app.core.scoping import can_read_company, can_write_company, is_super
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.ai_assessment import AIAssessment
from app.models.assessment_approval import AssessmentApproval

from app.schemas.ai_assessment import (
    AIAssessmentCreate,
    AIAssessmentOut,
    AIAssessmentListItem,
    AIAssessmentDiff,
)
from app.schemas.assessment_approval import (
    AssessmentApprovalCreate,
    AssessmentApprovalOut,
)

from app.crud.ai_assessment import (
    get_latest_for_system,
    list_versions_for_system,
    get_version,
    upsert_version_for_system,
    to_out,
)

from app.services.audit import audit_log, ip_from_request
from app.services.notifications import produce_assessment_approved

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


def _approval_to_out(
    approval: AssessmentApproval,
    *,
    assessment: AIAssessment,
    system: AISystem,
) -> AssessmentApprovalOut:
    """Shape ORM approval -> schema with system/company context."""
    return AssessmentApprovalOut.model_validate(
        {
            "id": approval.id,
            "assessment_id": approval.assessment_id,
            "ai_system_id": assessment.ai_system_id,
            "company_id": system.company_id,
            "approved_by": approval.approver_user_id,
            "approved_at": approval.approved_at,
            "note": approval.note,
            "created_at": approval.created_at,
        }
    )


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
    List all assessment versions (paginated, sortable).
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
    return [
        AIAssessmentListItem.model_validate(
            {
                "id": r.id,
                "system_id": r.ai_system_id,
                "risk_tier": r.risk_tier,
                "version_tag": getattr(r, "version_tag", None),
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
    Lightweight JSON diff between two versions (answers + risk_tier/version_tag changes).
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
    Create a new versioned assessment entry.
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


# -----------------------------
# Approve assessment (AR or SuperAdmin)
# -----------------------------
@router.post("/assessments/{assessment_id}/approve", response_model=AssessmentApprovalOut, status_code=status.HTTP_201_CREATED)
def approve_assessment(
    request: Request,  # must precede defaulted params for FastAPI/Pydantic
    assessment_id: int = Path(..., ge=1),
    payload: Optional[AssessmentApprovalCreate] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Approve an assessment.
    RBAC: Only the system's Authorized Representative or a Super Admin may approve.
    """
    # 1) Load assessment
    assessment = db.query(AIAssessment).filter(AIAssessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    # 2) Load system
    system = db.query(AISystem).filter(AISystem.id == assessment.ai_system_id).first()
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")

    # 3) RBAC: SuperAdmin OR system.authorized_representative_user_id == current_user.id
    if not is_super(current_user):
        ar_uid = getattr(system, "authorized_representative_user_id", None)
        if not ar_uid or int(ar_uid) != int(current_user.id):
            raise HTTPException(
                status_code=403,
                detail="Only the system's Authorized Representative or a Super Admin may approve assessments."
            )

    # 4) Insert approval record (protect against duplicates if uniqueness is enabled at DB level)
    note = payload.note if payload else None
    approval = AssessmentApproval(
        assessment_id=assessment.id,
        approver_user_id=current_user.id,
        note=note,
    )
    db.add(approval)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Likely unique constraint violation (one approval per assessment)
        raise HTTPException(status_code=409, detail="This assessment already has an approval.")
    db.refresh(approval)

    # 5) Optionally mirror onto assessment fields if they exist (idempotent/best-effort)
    try:
        changed = False
        if hasattr(assessment, "approved_by"):
            assessment.approved_by = current_user.id
            changed = True
        if hasattr(assessment, "approved_at"):
            from datetime import datetime as _dt
            assessment.approved_at = _dt.utcnow()
            changed = True
        if hasattr(assessment, "approval_note") and note is not None:
            assessment.approval_note = note
            changed = True
        if changed:
            db.add(assessment)
            db.commit()
    except Exception:
        db.rollback()

    # 6) Audit (best-effort)
    try:
        audit_log(
            db,
            company_id=getattr(system, "company_id", None),
            user_id=current_user.id,
            action="ASSESSMENT_APPROVED",
            entity_type="ai_assessment",
            entity_id=assessment.id,
            meta={
                "ai_system_id": assessment.ai_system_id,
                "approver_user_id": current_user.id,
                "note": note,
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    # 7) Notify (best-effort)
    try:
        produce_assessment_approved(
            db,
            company_id=getattr(system, "company_id", None),
            ai_system_id=assessment.ai_system_id,
            assessment_id=assessment.id,
            approver_user_id=current_user.id,
            note=note,
        )
    except Exception:
        pass

    return _approval_to_out(approval, assessment=assessment, system=system)


# -----------------------------
# List approvals for an assessment (RBAC: company read)
# -----------------------------
@router.get("/assessments/{assessment_id}/approvals", response_model=List[AssessmentApprovalOut])
def list_assessment_approvals(
    assessment_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all approval records for a given assessment (chronological order).
    """
    assessment = db.query(AIAssessment).filter(AIAssessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    system = db.query(AISystem).filter(AISystem.id == assessment.ai_system_id).first()
    if not system:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = (
        db.query(AssessmentApproval)
        .filter(AssessmentApproval.assessment_id == assessment_id)
        .order_by(AssessmentApproval.created_at.asc())
        .all()
    )
    return [_approval_to_out(r, assessment=assessment, system=system) for r in rows]