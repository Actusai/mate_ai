# app/schemas/incident.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal, Any, Dict

from pydantic import BaseModel, Field, conint, constr

# Allowed enums
Severity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["new", "investigating", "reported", "closed"]


class IncidentBase(BaseModel):
    """
    Base schema for incidents.
    """
    company_id: conint(ge=1) = Field(..., description="Owner company ID")
    ai_system_id: conint(ge=1) = Field(..., description="AI system ID")

    occurred_at: Optional[datetime] = Field(
        default=None, description="When the incident occurred (if known)"
    )
    severity: Optional[Severity] = Field(
        default=None, description="Impact level of the incident"
    )
    # free-form or taxonomy, e.g. 'malfunction', 'safety', 'security', 'data_breach'
    type: Optional[constr(strip_whitespace=True, max_length=50)] = Field(
        default=None, description="Incident type/category"
    )

    summary: constr(strip_whitespace=True, min_length=3, max_length=500) = Field(
        ..., description="Short summary of what happened"
    )
    details_json: Optional[Dict[str, Any]] = Field(
        default=None, description="Arbitrary key-value details describing the incident"
    )

    status: IncidentStatus = Field(
        default="new", description="Incident workflow status"
    )


class IncidentCreate(IncidentBase):
    """
    Create payload. 'reported_by' is taken from the authenticated user on the server side.
    """
    pass


class IncidentUpdate(BaseModel):
    """
    Partial update payload. All fields optional.
    """
    occurred_at: Optional[datetime] = None
    severity: Optional[Severity] = None
    type: Optional[constr(strip_whitespace=True, max_length=50)] = None
    summary: Optional[constr(strip_whitespace=True, min_length=3, max_length=500)] = None
    details_json: Optional[Dict[str, Any]] = None
    status: Optional[IncidentStatus] = None


class IncidentOut(IncidentBase):
    """
    Read model returned by the API.
    """
    id: int
    reported_by: Optional[int] = Field(
        default=None, description="User ID who reported the incident (server-populated)"
    )
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True