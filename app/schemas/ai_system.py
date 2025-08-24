# app/schemas/ai_system.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, conint, constr


# ---- Base ----
class AISystemBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=2, max_length=255)
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None  # e.g. development, production
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = None        # e.g. prohibited, high, limited, minimal
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None           # e.g. active, paused, retired
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None


# ---- Create / Update ----
class AISystemCreate(AISystemBase):
    # explicitno tražimo company_id u create-u
    company_id: conint(ge=1) = Field(..., description="ID of the client company that owns this AI system")


class AISystemUpdate(BaseModel):
    name: Optional[constr(strip_whitespace=True, min_length=2, max_length=255)] = None
    purpose: Optional[str] = None
    lifecycle_stage: Optional[constr(strip_whitespace=True, max_length=50)] = None
    risk_tier: Optional[constr(strip_whitespace=True, max_length=50)] = None
    status: Optional[constr(strip_whitespace=True, max_length=50)] = None
    owner_user_id: Optional[conint(ge=1)] = None
    notes: Optional[str] = None


# ---- Out ----
class AISystemOut(AISystemBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True