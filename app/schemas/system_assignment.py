# app/schemas/system_assignment.py
from datetime import datetime
from pydantic import BaseModel, conint

class SystemAssignmentBase(BaseModel):
    user_id: conint(ge=1)
    ai_system_id: conint(ge=1)

class SystemAssignmentCreate(SystemAssignmentBase):
    pass

class SystemAssignmentOut(SystemAssignmentBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True