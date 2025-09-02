# app/schemas/compliance_task.py
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, conint, constr

TaskStatus = Literal["open", "in_progress", "blocked", "postponed", "done"]
TaskSeverity = Literal["mandatory", "recommended"]

class ComplianceTaskBase(BaseModel):
    title: constr(strip_whitespace=True, min_length=3, max_length=255)
    description: Optional[str] = None
    status: TaskStatus = "open"
    severity: TaskSeverity = "mandatory"
    mandatory: bool = True

    owner_user_id: Optional[conint(ge=1)] = None
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    evidence_url: Optional[str] = None
    notes: Optional[str] = None

    # NOVO: pravni temelj (AI Act/GDPR/interna politika), max 255
    reference: Optional[constr(strip_whitespace=True, max_length=255)] = None

    # NOVO: koliko dana prije roka šaljemo podsjetnik (backend logika)
    # ostavljamo default 7, dopuštamo 0-365
    reminder_days_before: Optional[conint(ge=0, le=365)] = 7


class ComplianceTaskCreate(ComplianceTaskBase):
    company_id: conint(ge=1)
    ai_system_id: conint(ge=1)


class ComplianceTaskUpdate(BaseModel):
    # sve opcionalno
    title: Optional[constr(strip_whitespace=True, min_length=3, max_length=255)] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    severity: Optional[TaskSeverity] = None
    mandatory: Optional[bool] = None

    owner_user_id: Optional[conint(ge=1)] = None
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    evidence_url: Optional[str] = None
    notes: Optional[str] = None

    # NOVO
    reference: Optional[constr(strip_whitespace=True, max_length=255)] = None
    reminder_days_before: Optional[conint(ge=0, le=365)] = None


class ComplianceTaskOut(ComplianceTaskBase):
    id: int
    company_id: int
    ai_system_id: int
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
