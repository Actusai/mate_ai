# app/schemas/regulatory_deadline.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, conint, constr


# -----------------------------
# Create / Update payloads
# -----------------------------
class RegulatoryDeadlineCreate(BaseModel):
    company_id: conint(ge=1) = Field(..., description="Owner company ID")
    ai_system_id: Optional[conint(ge=1)] = Field(
        default=None, description="Optional AI system scope"
    )

    name: constr(strip_whitespace=True, min_length=3, max_length=255)
    kind: Optional[constr(strip_whitespace=True, max_length=50)] = Field(
        default=None, description="Categorization (e.g., 'ai_act_general', 'registration')"
    )
    due_date: datetime

    severity: Optional[Literal["low", "medium", "high", "critical"]] = None
    status: Literal["open", "done", "missed", "waived", "archived"] = "open"
    notes: Optional[str] = None


class RegulatoryDeadlineUpdate(BaseModel):
    ai_system_id: Optional[conint(ge=1)] = None

    name: Optional[constr(strip_whitespace=True, min_length=3, max_length=255)] = None
    kind: Optional[constr(strip_whitespace=True, max_length=50)] = None
    due_date: Optional[datetime] = None

    severity: Optional[Literal["low", "medium", "high", "critical"]] = None
    status: Optional[Literal["open", "done", "missed", "waived", "archived"]] = None
    notes: Optional[str] = None


# -----------------------------
# Response models
# -----------------------------
class RegulatoryDeadlineOut(BaseModel):
    id: int
    company_id: int
    ai_system_id: Optional[int] = None

    name: str
    kind: Optional[str] = None
    due_date: datetime

    severity: Optional[str] = None
    status: str
    notes: Optional[str] = None

    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# -----------------------------
# Optional: compact item for dashboard timeline
# -----------------------------
class RegulatoryTimelineItem(BaseModel):
    id: int
    name: str
    kind: Optional[str] = None
    due_date: datetime
    severity: Optional[str] = None
    status: str
    ai_system_id: Optional[int] = None

    class Config:
        from_attributes = True