# app/crud/compliance_task.py
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.compliance_task import ComplianceTask
from app.schemas.compliance_task import ComplianceTaskCreate, ComplianceTaskUpdate


def get_task(db: Session, task_id: int) -> Optional[ComplianceTask]:
    return db.query(ComplianceTask).filter(ComplianceTask.id == task_id).first()


def list_tasks_by_system(
    db: Session,
    system_id: int,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    reference: Optional[str] = None,   # partial, case-insensitive
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
        # Case-insensitive partial match, portable (SQLite/Postgres)
        like_value = f"%{reference.lower()}%"
        q = q.filter(func.lower(ComplianceTask.reference).like(like_value))

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

    ord_lower = (order or "asc").lower()
    if ord_lower == "desc":
        sort_col = sort_col.desc()
    # Stabilno sortiranje: tie-breaker po id
    return (
        q.order_by(sort_col, ComplianceTask.id.asc())
         .offset(skip)
         .limit(limit)
         .all()
    )


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
        # Ako klijent eksplicitno pošalje completed_at, poštujemo; inače None pa se može setati pri updateu.
        completed_at=payload.completed_at,
        evidence_url=payload.evidence_url,
        notes=payload.notes,
        reference=payload.reference,
        reminder_days_before=payload.reminder_days_before,
        created_by=user_id,
        updated_by=user_id,
    )
    # Auto-complete timestamp ako task već dolazi kao 'done' bez completed_at
    if obj.status == "done" and obj.completed_at is None:
        obj.completed_at = datetime.utcnow()

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_task(db: Session, obj: ComplianceTask, payload: ComplianceTaskUpdate, user_id: Optional[int] = None) -> ComplianceTask:
    data = payload.model_dump(exclude_unset=True)

    old_status = obj.status
    new_status = data.get("status", old_status)

    # Primijeni polja
    for k, v in data.items():
        setattr(obj, k, v)

    # Auto upravljanje completed_at ako klijent nije eksplicitno postavio
    if "completed_at" not in data:
        if old_status != "done" and new_status == "done":
            # prešli smo u done -> postavi timestamp
            obj.completed_at = obj.completed_at or datetime.utcnow()
        elif old_status == "done" and new_status != "done":
            # napustili 'done' -> očisti completed_at
            obj.completed_at = None

    obj.updated_by = user_id
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_task(db: Session, obj: ComplianceTask) -> None:
    db.delete(obj)
    db.commit()