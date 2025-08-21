from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

class InviteCreate(BaseModel):
    email: EmailStr
    company_id: int = Field(ge=1)
    package_id: int = Field(ge=1)
    role: str = Field(default="member")
    expires_in_days: int = Field(default=7, ge=1, le=60)

class InviteOut(BaseModel):
    id: int
    email: EmailStr
    token: str
    company_id: int
    package_id: int
    role: str
    status: str
    expires_at: datetime

    class Config:
        from_attributes = True  # pydantic v2: orm_mode replacement

class InviteAccept(BaseModel):
    token: str
    password: str = Field(min_length=6)
