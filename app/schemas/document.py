# app/schemas/document.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, Dict, Any, List, Literal

from pydantic import BaseModel, Field, conint, constr


# -----------------------------
# Base / CRUD
# -----------------------------
class DocumentBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=1, max_length=255)
    ai_system_id: Optional[conint(ge=1)] = Field(
        default=None, description="Optional AI system scope for the document"
    )
    type: Optional[constr(strip_whitespace=True, max_length=50)] = Field(
        default=None,
        description="e.g., architecture, datasets, rm_plan, testing, doc_pack_zip",
    )
    content_type: Optional[constr(strip_whitespace=True, max_length=120)] = None
    size_bytes: Optional[conint(ge=0)] = None
    storage_url: Optional[str] = Field(
        default=None, description="Path/URL to blob/object storage"
    )
    status: Optional[Literal["complete", "in_progress", "missing"]] = "in_progress"
    review_due_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Arbitrary metadata (stored as JSON)"
    )


class DocumentCreate(DocumentBase):
    company_id: conint(ge=1) = Field(..., description="Owner company ID")


class DocumentUpdate(BaseModel):
    # All fields optional (PATCH-like)
    name: Optional[constr(strip_whitespace=True, min_length=1, max_length=255)] = None
    ai_system_id: Optional[conint(ge=1)] = None
    type: Optional[constr(strip_whitespace=True, max_length=50)] = None
    content_type: Optional[constr(strip_whitespace=True, max_length=120)] = None
    size_bytes: Optional[conint(ge=0)] = None
    storage_url: Optional[str] = None
    status: Optional[Literal["complete", "in_progress", "missing"]] = None
    review_due_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class DocumentOut(DocumentBase):
    id: int
    company_id: int
    uploaded_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# -----------------------------
# Packs
# -----------------------------
class DocumentPackCreate(BaseModel):
    """
    Request body to generate a ZIP pack out of existing documents.
    You can select by explicit document IDs and/or by types.
    """
    ai_system_id: conint(ge=1)
    document_ids: Optional[List[conint(ge=1)]] = Field(
        default=None, description="Explicit document IDs to include"
    )
    types: Optional[List[constr(strip_whitespace=True, max_length=50)]] = Field(
        default=None, description="Include all documents of these types"
    )
    name: Optional[constr(strip_whitespace=True, max_length=255)] = Field(
        default=None, description="Optional display name for the generated pack"
    )