# app/schemas/assessment_approval.py
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, conint, constr


class AssessmentApprovalCreate(BaseModel):
    """Request body used to approve an assessment (AR / SuperAdmin)."""
    note: Optional[constr(strip_whitespace=True, max_length=1000)] = Field(
        default=None,
        description="Optional approval rationale/comment."
    )


class AssessmentApprovalOut(BaseModel):
    id: int = Field(..., description="Approval record ID.")
    assessment_id: conint(ge=1) = Field(..., description="Approved assessment ID.")
    approver_user_id: conint(ge=1) = Field(..., description="User ID of the approver.")
    note: Optional[str] = Field(default=None, description="Optional note provided by the approver.")
    approved_at: datetime = Field(..., description="UTC timestamp when the approval was issued.")
    created_at: datetime = Field(..., description="UTC timestamp when the approval record was created.")

    class Config:
        from_attributes = True