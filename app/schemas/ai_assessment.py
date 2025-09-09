# app/schemas/ai_assessment.py
from datetime import datetime
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, constr

# Reuse the same questionnaire schema as in schemas.ai_system
from app.schemas.ai_system import RiskAssessmentAnswer


# -----------------------------
# Create / request payloads
# -----------------------------
class AIAssessmentCreate(BaseModel):
    """
    Payload to create (and optionally persist) a new assessment version.
    - answers: set of boolean fields (same as /ai-systems/{id}/assessment)
    - version_tag: optional label (e.g., 'v1', 'Q3-2025')
    - save: if True, the endpoint will persist the version
    """
    answers: RiskAssessmentAnswer
    version_tag: Optional[constr(strip_whitespace=True, max_length=50)] = None
    save: bool = True


# -----------------------------
# Response models
# -----------------------------
class AIAssessmentOut(BaseModel):
    """
    One assessment version returned by the API.
    Fields align with risk_engine output + version metadata.
    """
    id: int
    system_id: int
    company_id: int

    # classification result
    risk_tier: constr(strip_whitespace=True, to_lower=True, min_length=3, max_length=20)
    obligations: Dict[str, List[str]]              # e.g. {"core": [...], "situational": [...]}
    rationale: List[str]                           # reasons for tier
    references: List[str] = []                     # short references (e.g. "Art. 9–15", "Art. 52")

    # snapshot of inputs
    answers: RiskAssessmentAnswer

    # version metadata
    version_tag: Optional[str] = None
    created_by: int
    created_at: datetime

    # --- sign-off (optional, mirrored on the assessment row if present) ---
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    approval_note: Optional[str] = None

    class Config:
        from_attributes = True


class AIAssessmentListItem(BaseModel):
    """
    Slim list item representation (used in paginated lists).
    """
    id: int
    system_id: int
    risk_tier: str
    version_tag: Optional[str] = None
    created_by: int
    created_at: datetime

    # Optional sign-off preview for list screens
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    approval_note: Optional[str] = None

    class Config:
        from_attributes = True


class AIAssessmentDetail(BaseModel):
    """
    Detailed view of one version (alternative to AIAssessmentOut).
    """
    id: int
    system_id: int
    company_id: int

    risk_tier: str
    obligations: Dict[str, List[str]]
    rationale: List[str]
    references: List[str] = []

    answers: RiskAssessmentAnswer

    version_tag: Optional[str] = None
    created_by: int
    created_at: datetime

    # Optional sign-off information
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    approval_note: Optional[str] = None

    class Config:
        from_attributes = True


# -----------------------------
# Diff output between two assessment versions
# -----------------------------
class AIAssessmentDiff(BaseModel):
    base_id: int
    compare_id: int

    risk_tier_from: Optional[str] = None
    risk_tier_to: Optional[str] = None
    version_tag_from: Optional[str] = None
    version_tag_to: Optional[str] = None

    added: Dict[str, Any]
    removed: Dict[str, Any]
    changed: Dict[str, Dict[str, Any]]  # {field: {"from": old, "to": new}}

    summary: Dict[str, int]  # {"added": X, "removed": Y, "changed": Z}