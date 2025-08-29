# app/schemas/ai_system.py
from datetime import datetime
from typing import Dict, List, Optional, Literal

from pydantic import BaseModel, Field, conint, constr, computed_field


# -----------------------------
# CRUD schemas for AI Systems
# -----------------------------
class AISystemBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=2, max_length=255)
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = None  # npr. "high_risk", "minimal_risk", ...
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None

    # ✅ Novo: dinamički status sukladnosti
    # Dozvoljene vrijednosti ostavljamo eksplicitno radi konzistentnosti u klijentu
    compliance_status: Optional[
        Literal["compliant", "partially_compliant", "non_compliant", "unknown"]
    ] = Field(
        default="unknown",
        description="Stanje usklađenosti AI sustava s obvezama (AI Act)."
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

    # ✅ Novo: dopušten i update compliance statusa
    compliance_status: Optional[
        Literal["compliant", "partially_compliant", "non_compliant", "unknown"]
    ] = None


class AISystemOut(AISystemBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    # ✅ Novo: izvedena metrika – ne sprema se u DB; računa se iz risk_tier + compliance_status
    @computed_field  # type: ignore[misc]
    @property
    def effective_risk_level(self) -> str:
        """
        Kombinira inherentni rizik (risk_tier) i stanje usklađenosti (compliance_status)
        u "operativni" rizik koji prikazujemo na UI-ju.
        """
        tier = (self.risk_tier or "").lower().strip()
        comp = (self.compliance_status or "unknown").lower().strip()

        if tier == "prohibited":
            return "prohibited_illegal"

        if tier in {"high_risk", "high"}:
            if comp == "compliant":
                return "controlled_high_risk"
            if comp == "partially_compliant":
                return "elevated_high_risk"
            if comp == "non_compliant":
                return "critical_risk"
            return "high_risk_unknown_compliance"

        if tier in {"limited_risk", "limited"}:
            if comp == "non_compliant":
                return "elevated_limited_risk"
            if comp == "partially_compliant":
                return "managed_limited_risk"
            if comp == "compliant":
                return "limited_risk_compliant"
            return "limited_risk_unknown_compliance"

        if tier in {"minimal_risk", "minimal", "out_of_scope"}:
            if comp == "non_compliant":
                return "formal_breach_low_risk"
            if comp == "partially_compliant":
                return "managed_low_risk"
            if comp == "compliant":
                return "low_risk_compliant"
            return "low_risk_unknown_compliance"

        # Fallback ako je tier nepoznat
        if comp == "non_compliant":
            return "unknown_tier_non_compliant"
        if comp == "partially_compliant":
            return "unknown_tier_partially_compliant"
        if comp == "compliant":
            return "unknown_tier_compliant"
        return "unknown_effective_risk"

    class Config:
        from_attributes = True


# --------------------------------
# Assessment (questionnaire) schemas
# --------------------------------
class RiskAssessmentAnswer(BaseModel):
    """
    Flat skup boolova koji koristi risk_engine.py.
    """
    # Scope / kontekst
    is_ai_system: bool = True                 # čl. 3 – ako False -> out_of_scope
    providers_outside_eu: bool = False        # dodaje situacijske obveze

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
    Tijelo koje POST-aš na /ai-systems/{system_id}/assessment
    """
    answers: RiskAssessmentAnswer


class RiskAssessmentResult(BaseModel):
    """
    Odgovor evaluacije.
    - obligations: map kategorija -> lista obveza
    - references: kratke pravne reference (npr. 'Art. 9–15', 'Annex III')
    """
    system_id: int
    risk_tier: constr(strip_whitespace=True, to_lower=True, min_length=3, max_length=20)
    obligations: Dict[str, List[str]]
    rationale: List[str]
    references: List[str] = []
    version: str = "1.1.0"