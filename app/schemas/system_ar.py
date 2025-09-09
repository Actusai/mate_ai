# app/schemas/system_ar.py
from __future__ import annotations
from pydantic import BaseModel, conint

class AssignARRequest(BaseModel):
    """Payload used to assign an Authorized Representative to an AI system."""
    user_id: conint(ge=1)