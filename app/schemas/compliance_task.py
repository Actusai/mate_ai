# app/schemas/compliance_task.py
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, conint, constr, ConfigDict

TaskStatus = Literal["open", "in_progress", "blocked", "postponed", "done"]
TaskSeverity = Literal["mandatory", "recommended"]  # add "nice_to_have" later if needed


class ComplianceTaskBase(BaseModel):
    title: constr(strip_whitespace=True, min_length=3, max_length=255) = Field(
        ..., description="Short task title."
    )
    description: Optional[str] = Field(None, description="Detailed task description.")
    status: TaskStatus = Field("open", description="Current task status.")
    severity: TaskSeverity = Field(
        "mandatory", description="Task severity (mandatory/recommended)."
    )
    mandatory: bool = Field(
        True, description="Convenience boolean reflecting severity=mandatory."
    )

    owner_user_id: Optional[conint(ge=1)] = Field(
        None, description="User ID of the task owner/assignee."
    )
    due_date: Optional[datetime] = Field(None, description="Due date/time (ISO 8601).")
    completed_at: Optional[datetime] = Field(
        None, description="Completion timestamp (ISO 8601)."
    )

    evidence_url: Optional[str] = Field(
        None, description="Link to evidence (document, URL, etc.)."
    )
    notes: Optional[str] = Field(None, description="Freeform notes for the task.")

    # Reference to legal/internal requirement (AI Act/GDPR/internal policy)
    reference: Optional[constr(strip_whitespace=True, max_length=255)] = Field(
        None, description="Reference to a requirement (AI Act/GDPR/policy)."
    )

    # How many days before due_date to send a reminder (used by backend job)
    reminder_days_before: Optional[conint(ge=0, le=365)] = Field(
        7, description="Days before due date to trigger a reminder (0-365)."
    )


class ComplianceTaskCreate(ComplianceTaskBase):
    company_id: conint(ge=1) = Field(
        ..., description="Company ID this task belongs to."
    )
    ai_system_id: conint(ge=1) = Field(
        ..., description="AI system ID this task is linked to."
    )


class ComplianceTaskUpdate(BaseModel):
    # All optional for PATCH
    title: Optional[constr(strip_whitespace=True, min_length=3, max_length=255)] = (
        Field(None, description="Short task title.")
    )
    description: Optional[str] = Field(None, description="Detailed task description.")
    status: Optional[TaskStatus] = Field(None, description="Current task status.")
    severity: Optional[TaskSeverity] = Field(
        None, description="Task severity (mandatory/recommended)."
    )
    mandatory: Optional[bool] = Field(
        None, description="Convenience boolean reflecting severity=mandatory."
    )

    owner_user_id: Optional[conint(ge=1)] = Field(
        None, description="User ID of the task owner/assignee."
    )
    due_date: Optional[datetime] = Field(None, description="Due date/time (ISO 8601).")
    completed_at: Optional[datetime] = Field(
        None, description="Completion timestamp (ISO 8601)."
    )

    evidence_url: Optional[str] = Field(
        None, description="Link to evidence (document, URL, etc.)."
    )
    notes: Optional[str] = Field(None, description="Freeform notes for the task.")

    reference: Optional[constr(strip_whitespace=True, max_length=255)] = Field(
        None, description="Reference to a requirement (AI Act/GDPR/policy)."
    )
    reminder_days_before: Optional[conint(ge=0, le=365)] = Field(
        None, description="Days before due date to trigger a reminder (0-365)."
    )


class ComplianceTaskOut(ComplianceTaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Task ID.")
    company_id: int = Field(..., description="Company ID this task belongs to.")
    ai_system_id: int = Field(..., description="AI system ID this task is linked to.")
    created_by: Optional[int] = Field(None, description="User ID who created the task.")
    updated_by: Optional[int] = Field(
        None, description="User ID who last updated the task."
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC).")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC).")
