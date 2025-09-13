# app/schemas/ai_system.py
from datetime import datetime
from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field, conint, constr


# -----------------------------
# CRUD schemas for AI Systems
# -----------------------------
class AISystemBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=2, max_length=255)
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = (
        None  # e.g., "high_risk", "minimal_risk"
    )
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None

    # Legacy/manual field (kept for compatibility). The platform also computes a
    # compliance status from tasks; that computed value is exposed via
    # AISystemOutExtended.compliance_status_computed.
    compliance_status: Optional[
        Literal["compliant", "partially_compliant", "non_compliant", "unknown"]
    ] = Field(
        default="unknown",
        description="(Legacy/manual) Compliance status of the AI system.",
    )


class AISystemCreate(AISystemBase):
    company_id: conint(ge=1) = Field(..., description="Owner company ID")


class AISystemUpdate(BaseModel):
    name: Optional[constr(strip_whitespace=True, min_length=2, max_length=255)] = None
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = None
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None

    # Legacy/manual field (kept for compatibility).
    compliance_status: Optional[
        Literal["compliant", "partially_compliant", "non_compliant", "unknown"]
    ] = None


# -----------------------------
# AR (Authorized Representative) read model
# -----------------------------
class AuthorizedRepresentativeOut(BaseModel):
    user_id: int
    email: Optional[str] = None


class AISystemOut(AISystemBase):
    id: int
    company_id: int

    # Kept for backward compatibility (read-only). Prefer 'authorized_representative' on extended schema.
    authorized_representative_user_id: Optional[int] = Field(
        default=None,
        description="(Deprecated) User ID of the Authorized Representative. Prefer 'authorized_representative' on extended schema.",
    )

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --------------------------------
# Extended output with computed badges
# --------------------------------
class AISystemOutExtended(AISystemOut):
    # Computed compliance status from tasks (mandatory coverage + overdue):
    # values align with the reporting helpers.
    compliance_status_computed: Optional[
        Literal["compliant", "at_risk", "non_compliant"]
    ] = Field(
        default=None,
        description="Computed compliance status based on mandatory tasks and overdue items.",
    )

    # Effective risk badge derived from (risk_tier, compliance_status_computed)
    effective_risk: Optional[Literal["low", "medium", "high", "critical"]] = Field(
        default=None,
        description="Effective risk derived from inherent risk tier and computed compliance status.",
    )

    # Structured AR object for convenient UI rendering (read-only).
    authorized_representative: Optional[AuthorizedRepresentativeOut] = Field(
        default=None,
        description="Authorized Representative of this AI system (read-only; assign via dedicated endpoints).",
    )


# --------------------------------
# Assessment (questionnaire) schemas
# --------------------------------
class RiskAssessmentAnswer(BaseModel):
    """
    Flat set of booleans consumed by risk_engine.py.
    """

    # Scope / context
    is_ai_system: bool = True  # Art. 3 – if False -> out_of_scope
    providers_outside_eu: bool = False  # adds situational obligations

    # Prohibited (Art. 5)
    subliminal_techniques: bool = False
    exploits_vulnerabilities: bool = False
    social_scoring_public_authorities: bool = False
    real_time_remote_biometric_id_in_public_for_law_enforcement: bool = False

    # High-risk (Annex III)
    critical_infrastructure: bool = False
    employment_hr: bool = False
    education: bool = False
    education_vocational_training: bool = False
    law_enforcement: bool = False
    migration_asylum_border: bool = False
    border_control_ai_assist: bool = False
    justice_democratic_processes: bool = False
    medical_device_or_care: bool = False
    biometric_identification_post: bool = False
    biometric_categorisation: bool = False
    credit_scoring_or_access_to_essential_services: bool = False
    essential_private_services: bool = False
    insurance_eligibility: bool = False

    # Limited-risk (Art. 52)
    content_generation_or_chatbot: bool = False
    deepfake_or_synthetic_media: bool = False
    emotion_recognition_non_le: bool = False


class RiskAssessmentRequest(BaseModel):
    """
    Body to POST to /ai-systems/{system_id}/assessment.
    """

    answers: RiskAssessmentAnswer


class RiskAssessmentResult(BaseModel):
    """
    Assessment response.
    - obligations: map category -> list of obligations
    - references: short legal references (e.g., 'Art. 9–15', 'Annex III')
    """

    system_id: int
    risk_tier: constr(strip_whitespace=True, to_lower=True, min_length=3, max_length=20)
    obligations: Dict[str, List[str]]
    rationale: List[str]
    references: List[str] = []
    version: str = "1.1.0"
