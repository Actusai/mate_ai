# app/schemas/calendar_pin.py
from __future__ import annotations

from typing import Optional, Literal
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


Visibility = Literal["company", "mate_internal"]


class CalendarPinBase(BaseModel):
    company_id: int = Field(..., ge=1, description="Company that owns the event")
    ai_system_id: Optional[int] = Field(
        None, ge=1, description="Optional AI system ID the event relates to"
    )
    title: str = Field(
        ..., min_length=1, max_length=255, description="Short event title"
    )
    description: Optional[str] = Field(
        None, description="Optional longer text/notes for the event"
    )
    start_at: datetime = Field(..., description="Event start (UTC, ISO8601)")
    end_at: Optional[datetime] = Field(None, description="Event end (UTC, ISO8601)")
    visibility: Visibility = Field(
        "company",
        description="Visibility scope: 'company' (client-facing) or 'mate_internal' (internal-only)",
    )
    severity: Optional[str] = Field(
        None,
        description="Optional severity tag (e.g., low|medium|high|critical) – free text",
    )
    status: Optional[str] = Field(
        "active",
        description="Lifecycle status (e.g., active|archived); free text",
    )

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        vv = (v or "company").strip().lower()
        if vv not in {"company", "mate_internal"}:
            raise ValueError(
                "Invalid visibility (must be 'company' or 'mate_internal')."
            )
        return vv

    @field_validator("status")
    @classmethod
    def _norm_status(cls, v: Optional[str]) -> Optional[str]:
        return (v or "active").strip().lower() if v is not None else None

    @field_validator("severity")
    @classmethod
    def _norm_severity(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _check_dates(self) -> "CalendarPinBase":
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at must be greater than or equal to start_at.")
        return self


class CalendarPinCreate(CalendarPinBase):
    """
    Payload for creating a calendar pin.
    """

    pass


class CalendarPinUpdate(BaseModel):
    """
    Partial update. Provide only fields you want to change.
    """

    ai_system_id: Optional[int] = Field(None, ge=1)
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    visibility: Optional[Visibility] = None
    severity: Optional[str] = None
    status: Optional[str] = None

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        vv = v.strip().lower()
        if vv not in {"company", "mate_internal"}:
            raise ValueError(
                "Invalid visibility (must be 'company' or 'mate_internal')."
            )
        return vv

    @field_validator("status")
    @classmethod
    def _norm_status(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("severity")
    @classmethod
    def _norm_severity(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _check_dates(self) -> "CalendarPinUpdate":
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("end_at must be greater than or equal to start_at.")
        return self


class CalendarPinOut(CalendarPinBase):
    id: int = Field(..., ge=1)
    created_by_user_id: Optional[int] = None
    updated_by_user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True,  # ORM mode
        "populate_by_name": True,
    }
