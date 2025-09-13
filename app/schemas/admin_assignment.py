# app/schemas/admin_assignment.py

from pydantic import BaseModel, Field, conint
from typing import Optional
from datetime import datetime

# ---------- Base ----------


class AdminAssignmentBase(BaseModel):
    company_id: conint(gt=0) = Field(
        ..., description="ID of the company this admin supports"
    )
    admin_user_id: conint(gt=0) = Field(
        ..., description="User ID of the admin (must have role 'admin')"
    )


# ---------- Create / Delete payloads ----------


class AdminAssignmentCreate(AdminAssignmentBase):
    """Payload to assign an admin to a company."""

    pass


class AdminAssignmentDelete(BaseModel):
    """Payload to revoke an assignment (optional if using path params)."""

    assignment_id: conint(gt=0)


# ---------- Read models ----------


class AdminAssignmentOut(BaseModel):
    id: int
    company_id: int
    admin_user_id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # Pydantic v2-friendly: map from ORM objects


# ---------- Query helpers (optional but handy in Swagger) ----------


class AdminAssignmentQueryByCompany(BaseModel):
    company_id: conint(gt=0)


class AdminAssignmentQueryByAdmin(BaseModel):
    admin_user_id: conint(gt=0)
