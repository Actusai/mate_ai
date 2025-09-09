# app/crud/ai_system.py
from typing import List, Optional, Sequence
from datetime import datetime

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

    # Priprema podataka + default za compliance_status (DB kolona je NOT NULL)
    data = payload.model_dump(exclude_none=True)
    data.setdefault("compliance_status", "pending")

    # (opcionalno) inicijalna aktivnost na kreiranju
    if "last_activity_at" not in data:
        data["last_activity_at"] = datetime.utcnow()

    obj = AISystem(**data)

    # Ako je eksplicitno poslan compliance_status ili compliance_score,
    # možemo postaviti i timestamp za praćenje promjene
    if "compliance_status" in data or "compliance_score" in data:
        obj.compliance_updated_at = datetime.utcnow()

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_system(
    db: Session,
    obj: AISystem,
    payload: AISystemUpdate,
) -> AISystem:
    # snimi stare vrijednosti za detekciju promjene
    old_status = getattr(obj, "compliance_status", None)
    old_score = getattr(obj, "compliance_score", None)

    # ažuriramo samo poslana polja
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    # ako se promijenio compliance_status ili compliance_score -> osvježi timestamp
    new_status = getattr(obj, "compliance_status", None)
    new_score = getattr(obj, "compliance_score", None)
    if (new_status is not None and new_status != old_status) or (new_score is not None and new_score != old_score):
        obj.compliance_updated_at = datetime.utcnow()

    # svaka izmjena bilježi aktivnost
    obj.last_activity_at = datetime.utcnow()

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_system(db: Session, obj: AISystem) -> None:
    db.delete(obj)
    db.commit()