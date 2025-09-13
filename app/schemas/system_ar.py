# app/schemas/system_ar.py
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, conint, model_validator


class AssignARRequest(BaseModel):
    """Payload used to assign an Authorized Representative to an AI system."""

    user_id: conint(ge=1)


# ---------------------------
# Bulk AR transfer schemas
# ---------------------------
class BulkTransferFilter(BaseModel):
    """
    Optional filters when selecting systems for bulk AR transfer.
    All filters are combined with AND (when provided).
    """

    risk_tier: Optional[str] = Field(
        default=None,
        description="Only systems with this risk_tier (e.g., 'high_risk', 'minimal_risk').",
    )
    lifecycle_stage: Optional[str] = Field(
        default=None, description="Only systems with this lifecycle_stage."
    )
    status: Optional[str] = Field(
        default=None, description="Only systems with this status."
    )
    from_user_id: Optional[conint(ge=1)] = Field(
        default=None,
        description="If provided, only systems currently assigned to this AR will be transferred.",
    )
    name_ilike: Optional[str] = Field(
        default=None,
        description="Case-insensitive name contains filter (database ILIKE / LIKE).",
    )


class BulkTransferRequest(BaseModel):
    """
    Bulk AR reassignment for multiple systems.
    Target set is chosen by either explicit system_ids, or by (company_id + optional filters).
    """

    to_user_id: conint(ge=1) = Field(..., description="New AR user ID.")
    company_id: Optional[conint(ge=1)] = Field(
        default=None,
        description="Scope systems to this company when using filters (required if system_ids is not provided).",
    )
    system_ids: Optional[List[conint(ge=1)]] = Field(
        default=None,
        description="Explicit system IDs to transfer. If present, filters are applied only to these IDs (intersection).",
    )
    filter: Optional[BulkTransferFilter] = Field(
        default=None, description="Additional filters to narrow the selection."
    )
    handover: bool = Field(
        default=False,
        description="If true, record AR_HANDOVER audit events when replacing an existing AR.",
    )
    reassign_open_tasks: bool = Field(
        default=False,
        description="If true, reassign open compliance tasks from the old AR to the new AR.",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, do not change data—return a preview of what would be updated.",
    )

    @model_validator(mode="after")
    def _validate_target_selection(self) -> "BulkTransferRequest":
        # Require either system_ids OR (company_id with/without filters).
        if not self.system_ids and not self.company_id:
            raise ValueError(
                "Provide either 'system_ids' or 'company_id' (with optional 'filter')."
            )
        return self


class SkippedItem(BaseModel):
    system_id: conint(ge=1)
    reason: str


class BulkTransferResult(BaseModel):
    """
    Summary of bulk transfer execution (or dry-run preview).
    """

    total_scanned: int = Field(..., description="How many systems were considered.")
    updated: int = Field(
        ..., description="How many systems were (or would be) reassigned to the new AR."
    )
    tasks_reassigned: int = Field(
        ...,
        description="How many tasks were (or would be) reassigned if reassign_open_tasks=True.",
    )
    updated_system_ids: List[int] = Field(
        default_factory=list,
        description="List of system IDs updated (or that would be updated in dry run).",
    )
    skipped: List[SkippedItem] = Field(
        default_factory=list, description="Systems that were skipped with reasons."
    )
    dry_run: bool = Field(
        False, description="True if this is a dry run result (no changes applied)."
    )
    to_user_id: Optional[int] = Field(
        None, description="Target AR user ID for reference."
    )
