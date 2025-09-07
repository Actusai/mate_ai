# app/schemas/document.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional, Literal

from pydantic import BaseModel, Field, HttpUrl, conint, constr, model_validator


# -----------------------------
# Base / Create / Update / Out
# -----------------------------
class DocumentBase(BaseModel):
    document_type: constr(strip_whitespace=True, max_length=50) = Field(
        default="evidence", description="Logical type, e.g. 'evidence', 'policy', 'report', 'other'."
    )
    version: Optional[constr(strip_whitespace=True, max_length=50)] = None
    effective_date: Optional[date] = None
    url: constr(strip_whitespace=True, min_length=3) = Field(
        ..., description="Publicly reachable URL to the document or evidence."
    )


class DocumentCreate(DocumentBase):
    """
    Create-time: at least one of (ai_system_id, task_id) must be provided.
    company_id is inferred from the referenced system/task on the backend.
    """
    ai_system_id: Optional[conint(ge=1)] = Field(default=None, description="Link to AI system.")
    task_id: Optional[conint(ge=1)] = Field(default=None, description="Link to a specific compliance task (evidence).")

    @model_validator(mode="after")
    def _validate_links(self):
        if not (self.ai_system_id or self.task_id):
            raise ValueError("Either 'ai_system_id' or 'task_id' must be provided.")
        return self


class DocumentUpdate(BaseModel):
    document_type: Optional[constr(strip_whitespace=True, max_length=50)] = None
    version: Optional[constr(strip_whitespace=True, max_length=50)] = None
    effective_date: Optional[date] = None
    url: Optional[constr(strip_whitespace=True, min_length=3)] = None


class DocumentOut(DocumentBase):
    id: int
    company_id: int
    ai_system_id: Optional[int] = None
    task_id: Optional[int] = None

    uploaded_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True