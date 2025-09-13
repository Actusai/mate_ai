# app/schemas/package.py
from pydantic import BaseModel, Field, conint
from typing import Optional


class PackageBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    price: conint(ge=0) = 0

    # 0 = unlimited
    ai_system_limit: conint(ge=0) = 0
    user_limit: conint(ge=0) = 0
    client_limit: conint(ge=0) = 0

    # samo AR ponude
    is_ar_only: bool = False


class PackageCreate(PackageBase):
    pass


class PackageUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    price: Optional[conint(ge=0)] = None
    ai_system_limit: Optional[conint(ge=0)] = None
    user_limit: Optional[conint(ge=0)] = None
    client_limit: Optional[conint(ge=0)] = None
    is_ar_only: Optional[bool] = None


class PackageOut(PackageBase):
    id: int

    class Config:
        from_attributes = True
