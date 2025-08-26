# app/schemas/ai_system.py
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, conint, constr

# -----------------------------
# CRUD schemas for AI Systems
# -----------------------------
class AISystemBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=2, max_length=255)
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = None
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None


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


class AISystemOut(AISystemBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --------------------------------
# Assessment (questionnaire) schemas
# --------------------------------
class RiskAssessmentAnswer(BaseModel):
    """
    Flat booleans koje koristi risk_engine.py.
    GET /assessment-sample može vam vratiti primjer (sve False).
    """

    # Scope / kontekst
    is_ai_system: bool = True                     # čl. 3 definicija AI sustava
    providers_outside_eu: bool = False           # za situacijske obveze (EU Authorized Rep)

    # Prohibited (Art. 5)
    subliminal_techniques: bool = False
    exploits_vulnerabilities: bool = False
    social_scoring_public_authorities: bool = False
    real_time_remote_biometric_id_in_public_for_law_enforcement: bool = False

    # High-risk (Annex III) – dopunjeno
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
    Tijelo za POST /ai-systems/{system_id}/assessment

    Primjer:
    {
      "answers": {
        "content_generation_or_chatbot": true
      }
    }
    """
    answers: RiskAssessmentAnswer


class RiskAssessmentResult(BaseModel):
    """
    Odgovor procjene rizika.
    - obligations: dict(category -> list)
    - references: popis članaka/annexa
    """
    system_id: int
    risk_tier: constr(strip_whitespace=True, to_lower=True, min_length=3, max_length=20)
    obligations: Dict[str, List[str]]
    rationale: List[str]
    references: Optional[List[str]] = None
    version: str = "1.1.0"