# app/api/v1/systems.py
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super,
    is_admin,
    is_contributor,
    can_write_company,
    get_assigned_company_ids,
    get_assigned_system_ids,
    can_read_system,
    can_write_system_full,
    can_write_system_limited,
)
from app.models.user import User
from app.models.ai_system import AISystem
from app.schemas.ai_system import (
    AISystemCreate,
    AISystemUpdate,
    AISystemOut,
    RiskAssessmentAnswer,
    RiskAssessmentResult,
)
from app.crud.ai_system import (
    get_system as crud_get_system,
    get_all_systems as crud_get_all_systems,
    get_systems_by_company_ids as crud_get_systems_by_company_ids,
    create_system as crud_create_system,
    update_system as crud_update_system,
    delete_system as crud_delete_system,
)

# Quick risk engine klasifikacija (bez spremanja verzija)
from app.services.risk_engine import classify_ai_system

router = APIRouter()

# Polja koja Contributor smije mijenjati (svjesno NE uključujemo compliance_status)
CONTRIBUTOR_ALLOWED_FIELDS = {"notes", "status", "lifecycle_stage"}


# ---------------------------
# Helpers
# ---------------------------
def _to_out(s: AISystem) -> AISystemOut:
    # Pydantic v2, from_attributes=True u shemi
    return AISystemOut.model_validate(s)


# ---------------------------
# CRUD
# ---------------------------
@router.get("/ai-systems", response_model=List[AISystemOut])
def list_ai_systems(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    company_id: Optional[int] = Query(
        None, description="Optional filter: only systems for this company_id"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    List AI systems.
    - super_admin: svi sustavi (uz opcionalni filter po company_id)
    - staff/client admin: sustavi vidljivih kompanija (vlastita; staff admini i dodijeljene)
    - contributor: samo eksplicitno dodijeljeni AI sustavi
    """
    if is_super(current_user):
        rows = (
            crud_get_systems_by_company_ids(db, [company_id], skip=skip, limit=limit)
            if company_id is not None
            else crud_get_all_systems(db, skip=skip, limit=limit)
        )
        return [_to_out(r) for r in rows]

    # contributor: listati po dodijeljenim sustavima
    if is_contributor(current_user):
        assigned_ids = get_assigned_system_ids(db, current_user.id)
        if not assigned_ids:
            return []
        rows = (
            db.query(AISystem)
            .filter(AISystem.id.in_(assigned_ids))
            .order_by(AISystem.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return [_to_out(r) for r in rows]

    # staff/client admin: vidljive kompanije
    visible_company_ids = set()
    if current_user.company_id:
        visible_company_ids.add(current_user.company_id)
    if is_admin(current_user):  # staff admin dobiva i dodijeljene kompanije
        visible_company_ids.update(get_assigned_company_ids(db, current_user.id))

    if company_id is not None:
        if company_id not in visible_company_ids:
            return []
        visible_company_ids = {company_id}

    if not visible_company_ids:
        return []

    rows = crud_get_systems_by_company_ids(db, list(visible_company_ids), skip=skip, limit=limit)
    return [_to_out(r) for r in rows]


@router.post("/ai-systems", response_model=AISystemOut, status_code=status.HTTP_201_CREATED)
def create_ai_system(
    payload: AISystemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Kreiranje AI sustava.
    - super_admin: bilo koja kompanija
    - client admin: vlastita kompanija
    - staff admin: samo dodijeljene kompanije
    - contributor: nema prava kreiranja
    Napomena: compliance_status (ako je poslan u payloadu) smije postaviti samo onaj tko ima pravo pisanja nad kompanijom.
    """
    if is_contributor(current_user):
        raise HTTPException(status_code=403, detail="Contributors cannot create AI systems")

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

    if not can_read_system(db, current_user, obj):
        raise HTTPException(status_code=403, detail="Forbidden")

    return _to_out(obj)


@router.put("/ai-systems/{system_id}", response_model=AISystemOut)
def update_ai_system(
    system_id: int,
    payload: AISystemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ažuriranje AI sustava.
    - full edit: super_admin, client admin (vlastita), staff admin (dodijeljene)
    - limited edit: contributor ako je dodijeljen (samo: notes, status, lifecycle_stage)
    """
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_write_system_limited(db, current_user, obj):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    data = payload.model_dump(exclude_none=True)

    # Contributor smije mijenjati samo ograničeni skup polja
    if not can_write_system_full(db, current_user, obj):
        illegal = set(data.keys()) - CONTRIBUTOR_ALLOWED_FIELDS
        if illegal:
            allowed = ", ".join(sorted(CONTRIBUTOR_ALLOWED_FIELDS))
            raise HTTPException(
                status_code=403,
                detail=f"Contributors can only update: {allowed}",
            )

    obj = crud_update_system(db, obj, payload)
    return _to_out(obj)


@router.delete("/ai-systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Brisanje AI sustava.
    - super_admin: da
    - client admin: da (za vlastitu kompaniju)
    - staff admin: da (za dodijeljene kompanije)
    - contributor: ne
    """
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_write_system_full(db, current_user, obj):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    crud_delete_system(db, obj)
    return None


# ---------------------------
# Risk Assessment – quick classify (bez verzioniranja)
# ---------------------------
@router.get("/ai-systems/{system_id}/assessment-sample", response_model=RiskAssessmentAnswer)
def get_assessment_sample(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Vraća primjer tijela upita (sva polja na False) za POST assessment.
    """
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_read_system(db, current_user, obj):
        raise HTTPException(status_code=403, detail="Forbidden")

    return RiskAssessmentAnswer.model_validate({})


@router.post("/ai-systems/{system_id}/assessment", response_model=RiskAssessmentResult)
def assess_ai_system(
    system_id: int,
    payload: RiskAssessmentAnswer,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Klasificira AI sustav po razini rizika na temelju odgovora na upitnik.
    Bilo tko tko smije ČITATI sustav smije pokrenuti procjenu (bez spremanja verzija).
    Za verzionirane procjene koristi rute iz app/api/v1/assessments.py.
    """
    obj = crud_get_system(db, system_id)
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")

    if not can_read_system(db, current_user, obj):
        raise HTTPException(statusocode=403, detail="Forbidden")

    answers_dict: Dict[str, Any] = payload.model_dump(exclude_none=True)
    result_dict = classify_ai_system(answers_dict)

    obligations = result_dict.get("obligations", {})
    if isinstance(obligations, list):
        obligations = {"core": obligations, "situational": []}

    return RiskAssessmentResult(
        system_id=system_id,
        risk_tier=result_dict.get("risk_tier", "minimal_risk"),
        obligations=obligations,
        rationale=result_dict.get("rationale", []),
        version="1.1.0",
    )