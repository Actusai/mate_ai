# app/schemas/system_assignment.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class SystemAssignmentOut(BaseModel):
    id: int
    user_id: int
    ai_system_id: int
    created_at: datetime

    # NEW: optional role on the assignment (e.g. "authorized_representative", "admin", "member")
    role: Optional[str] = None

    class Config:
        from_attributes = True


# --- Nested user summary for dashboards/lists ---
class UserMini(BaseModel):
    id: int
    email: str
    role: str
    full_name: Optional[str] = None

    class Config:
        from_attributes = True


class SystemAssignmentDetailedOut(SystemAssignmentOut):
    user: UserMini