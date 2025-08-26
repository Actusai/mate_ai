from typing import Optional, List
from pydantic import BaseModel, Field

# Minimal MVP questionnaire (add more later)
class AssessmentAnswers(BaseModel):
    provider_in_eu: bool = Field(..., description="Is the provider established in the EU?")
    placed_on_eu_market: bool = Field(..., description="Will the system be placed on the EU market or put into service in the EU?")
    is_gpai: bool = False
    uses_biometric_identification: bool = False
    biometric_is_remote_realtime: bool = False
    social_scoring: bool = False
    emotion_recognition_in_workplace_or_education: bool = False
    critical_infrastructure_management: bool = False
    law_enforcement_use: bool = False
    migration_asylum_border: bool = False
    employment_or_education_impact: bool = False
    medical_or_healthcare: bool = False
    credit_scoring_or_access_to_essentials: bool = False

class AIAssessmentCreate(BaseModel):
    answers: AssessmentAnswers

class AIAssessmentOut(BaseModel):
    id: int
    system_id: int
    company_id: int
    answers: AssessmentAnswers
    risk_tier: Optional[str]
    prohibited: bool
    high_risk: bool
    obligations: List[str]

    class Config:
        from_attributes = True