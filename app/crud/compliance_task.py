# app/crud/compliance_task.py
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.compliance_task import ComplianceTask
from app.schemas.compliance_task import ComplianceTaskCreate, ComplianceTaskUpdate

def get_task(db: Session, task_id: int) -> Optional[ComplianceTask]:
    return db.query(ComplianceTask).filter(ComplianceTask.id == task_id).first()

def list_tasks_by_system(
    db: Session,
    system_id: int,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    owner_user_id: Optional[int] = None,      # NOVO
    reference: Optional[str] = None,           # NOVO
    skip: int = 0,
    limit: int = 50,
    sort_by: str = "due_date",
    order: str = "asc",
) -> List[ComplianceTask]:
    q = db.query(ComplianceTask).filter(ComplianceTask.ai_system_id == system_id)

    if status:
        q = q.filter(ComplianceTask.status == status)
    if severity:
        q = q.filter(ComplianceTask.severity == severity)
    if owner_user_id:
        q = q.filter(ComplianceTask.owner_user_id == owner_user_id)
    if reference:
        # partial match; SQLite podržava ILIKE simulacijom preko lower()
        like_value = f"%{reference}%"
        q = q.filter(ComplianceTask.reference.ilike(like_value))  # za Postgres
        # Ako si striktno na SQLite i nemaš ilike: koristi donju liniju umjesto gornje
        # q = q.filter(func.lower(ComplianceTask.reference).like(func.lower(like_value)))

    allowed_sort_cols = {
        "id": ComplianceTask.id,
        "title": ComplianceTask.title,
        "status": ComplianceTask.status,
        "severity": ComplianceTask.severity,
        "mandatory": ComplianceTask.mandatory,
        "owner_user_id": ComplianceTask.owner_user_id,
        "due_date": ComplianceTask.due_date,
        "completed_at": ComplianceTask.completed_at,
        "created_at": ComplianceTask.created_at,
        "updated_at": ComplianceTask.updated_at,
        "reference": ComplianceTask.reference,
        "reminder_days_before": ComplianceTask.reminder_days_before,
    }
    sort_col = allowed_sort_cols.get(sort_by, ComplianceTask.due_date)
    if order and order.lower() == "desc":
        sort_col = sort_col.desc()

    return q.order_by(sort_col).offset(skip).limit(limit).all()

def create_task(db: Session, payload: ComplianceTaskCreate, user_id: Optional[int] = None) -> ComplianceTask:
    obj = ComplianceTask(
        company_id=payload.company_id,
        ai_system_id=payload.ai_system_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        severity=payload.severity,
        mandatory=payload.mandatory,
        owner_user_id=payload.owner_user_id,
        due_date=payload.due_date,
        completed_at=payload.completed_at,
        evidence_url=payload.evidence_url,
        notes=payload.notes,
        reference=payload.reference,                         # NOVO
        reminder_days_before=payload.reminder_days_before,   # NOVO
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def update_task(db: Session, obj: ComplianceTask, payload: ComplianceTaskUpdate, user_id: Optional[int] = None) -> ComplianceTask:
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    obj.updated_by = user_id
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def delete_task(db: Session, obj: ComplianceTask) -> None:
    db.delete(obj)
    db.commit()
