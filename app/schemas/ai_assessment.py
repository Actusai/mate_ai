# app/schemas/ai_assessment.py
from datetime import datetime
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, constr

# Koristimo postojeći upitnik iz schemas.ai_system (isti kao /systems assessment)
from app.schemas.ai_system import RiskAssessmentAnswer


# -----------------------------
# Create / request payloads
# -----------------------------
class AIAssessmentCreate(BaseModel):
    """
    Payload za kreiranje (i po potrebi spremanje) nove verzije procjene.
    - answers: skup boolean polja (isti kao u /ai-systems/{id}/assessment)
    - version_tag: opcionalna oznaka (npr. 'v1', 'Q3-2025')
    - save: ako je True, endpoint će trajno spremiti verziju
    """
    answers: RiskAssessmentAnswer
    version_tag: Optional[constr(strip_whitespace=True, max_length=50)] = None
    save: bool = True


# -----------------------------
# Response modeli
# -----------------------------
class AIAssessmentOut(BaseModel):
    """
    Jedna verzija procjene koju vraća API.
    Polja su usklađena s risk_engine outputom + metapodaci o verziji.
    """
    id: int
    system_id: int
    company_id: int

    # rezultat klasifikacije
    risk_tier: constr(strip_whitespace=True, to_lower=True, min_length=3, max_length=20)
    obligations: Dict[str, List[str]]              # npr. {"core": [...], "situational": [...]}
    rationale: List[str]                           # objašnjenja (zašto je u tom tieru)
    references: List[str] = []                     # kratke reference (npr. "Art. 9–15", "Art. 52")

    # snimka inputa
    answers: RiskAssessmentAnswer

    # metapodaci o verziji
    version_tag: Optional[str] = None
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class AIAssessmentListItem(BaseModel):
    """
    Sažetak za liste – manji payload.
    """
    id: int
    system_id: int
    risk_tier: str
    version_tag: Optional[str] = None
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


class AIAssessmentDetail(BaseModel):
    """
    Detaljan prikaz pojedine verzije (alternativa AIAssessmentOut).
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

    class Config:
        from_attributes = True


# --- Diff output between two assessment versions ---
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class AIAssessmentDiff(BaseModel):
    base_id: int
    compare_id: int

    risk_tier_from: Optional[str] = None
    risk_tier_to: Optional[str] = None
    version_tag_from: Optional[str] = None
    version_tag_to: Optional[str] = None

    added: Dict[str, Any]
    removed: Dict[str, Any]
    changed: Dict[str, Dict[str, Any]]  # { field: {"from": old, "to": new} }

    summary: Dict[str, int]  # {"added": X, "removed": Y, "changed": Z}