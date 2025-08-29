# app/crud/ai_system.py
from typing import List, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.ai_system import AISystem
from app.models.company import Company
from app.schemas.ai_system import AISystemCreate, AISystemUpdate


# --- Read helpers -------------------------------------------------------------

def get_system(db: Session, system_id: int) -> Optional[AISystem]:
    return db.query(AISystem).filter(AISystem.id == system_id).first()


def get_systems_by_company_ids(
    db: Session,
    company_ids: Sequence[int],
    skip: int = 0,
    limit: int = 50,
) -> List[AISystem]:
    if not company_ids:
        return []
    return (
        db.query(AISystem)
        .filter(AISystem.company_id.in_(company_ids))
        .order_by(AISystem.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_all_systems(
    db: Session,
    skip: int = 0,
    limit: int = 50,
) -> List[AISystem]:
    return (
        db.query(AISystem)
        .order_by(AISystem.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


# --- Create / Update / Delete ------------------------------------------------

def create_system(db: Session, payload: AISystemCreate) -> AISystem:
    # Ensure company exists
    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        raise ValueError("Company not found")

    obj = AISystem(
        company_id=payload.company_id,
        name=payload.name,
        purpose=payload.purpose,
        lifecycle_stage=payload.lifecycle_stage,
        risk_tier=payload.risk_tier,
        status=payload.status,
        owner_user_id=payload.owner_user_id,
        notes=payload.notes,
        # NEW: podrška za compliance_status (ako je poslan u payloadu)
        compliance_status=getattr(payload, "compliance_status", None),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_system(
    db: Session,
    obj: AISystem,
    payload: AISystemUpdate,
) -> AISystem:
    # ažuriramo samo polja koja su poslana (uključujući compliance_status)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_system(db: Session, obj: AISystem) -> None:
    db.delete(obj)
    db.commit()