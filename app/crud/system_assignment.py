# app/crud/system_assignment.py
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.system_assignment import SystemAssignment

def get_assignment(db: Session, user_id: int, ai_system_id: int) -> Optional[SystemAssignment]:
    return (
        db.query(SystemAssignment)
        .filter(SystemAssignment.user_id == user_id, SystemAssignment.ai_system_id == ai_system_id)
        .first()
    )

def get_assignments_for_system(db: Session, ai_system_id: int) -> List[SystemAssignment]:
    return (
        db.query(SystemAssignment)
        .filter(SystemAssignment.ai_system_id == ai_system_id)
        .order_by(SystemAssignment.id.desc())
        .all()
    )

def get_assignments_for_user(db: Session, user_id: int) -> List[SystemAssignment]:
    return (
        db.query(SystemAssignment)
        .filter(SystemAssignment.user_id == user_id)
        .order_by(SystemAssignment.id.desc())
        .all()
    )

def create_assignment(db: Session, user_id: int, ai_system_id: int) -> SystemAssignment:
    existing = get_assignment(db, user_id, ai_system_id)
    if existing:
        return existing
    obj = SystemAssignment(user_id=user_id, ai_system_id=ai_system_id)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def delete_assignment(db: Session, obj: SystemAssignment) -> None:
    db.delete(obj)
    db.commit()

def get_assigned_system_ids_for_user(db: Session, user_id: int) -> List[int]:
    rows = db.query(SystemAssignment.ai_system_id).filter(SystemAssignment.user_id == user_id).all()
    return [sid for (sid,) in rows]