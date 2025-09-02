## app/schemas/user.py
from pydantic import BaseModel, EmailStr
from typing import Optional


class UserOut(BaseModel):
    id: int
    email: EmailStr
    role: str
    company_id: Optional[int] = None
    company_name: Optional[str] = None  # Dodano

    class Config:
        from_attributes = True