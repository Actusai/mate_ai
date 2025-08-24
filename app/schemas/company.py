from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class CompanyBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    address: Optional[str] = None
    country: Optional[str] = None
    legal_form: Optional[str] = None
    registration_number: Optional[str] = None
    website: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    contact_person: Optional[str] = None
    is_authorized_representative: Optional[bool] = False

class CompanyCreate(CompanyBase):
    name: str = Field(..., min_length=2, max_length=255)

class CompanyUpdate(CompanyBase):
    pass

class CompanyOut(CompanyBase):
    id: int

    class Config:
        from_attributes = True