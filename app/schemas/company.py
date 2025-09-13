from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field, model_validator


# Dozvoljeni tipovi tvrtke (usklađeno s EU AI Act operatorima)
CompanyType = Literal[
    "authorized_representative",
    "deployer",
    "developer",
    "importer",
    "distributor",
]


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

    # BACK-COMPAT: zadržavamo zastavicu koju već koristi backend/DB
    is_authorized_representative: Optional[bool] = False

    # NOVO: tip tvrtke (default = deployer)
    company_type: CompanyType = "deployer"

    @model_validator(mode="after")
    def _sync_is_ar_and_company_type(self):
        """
        Back-compat sinkronizacija:
        - Ako je is_authorized_representative True i company_type nije zadano/je default,
          postavi company_type na 'authorized_representative'.
        - Ako je company_type 'authorized_representative', osiguraj da je zastavica True.
        """
        try:
            if self.is_authorized_representative:
                # korisnik je eksplicitno označio AR; ne prepisuj ako je već neki drugi tip
                if self.company_type == "deployer" or self.company_type is None:
                    object.__setattr__(
                        self, "company_type", "authorized_representative"
                    )
            if (
                self.company_type == "authorized_representative"
                and not self.is_authorized_representative
            ):
                object.__setattr__(self, "is_authorized_representative", True)
        except Exception:
            # ne ruši validaciju zbog sync logike
            pass
        return self


class CompanyCreate(CompanyBase):
    # name je već required u CompanyBase; ovdje samo eksplicitno naglašeno
    name: str = Field(..., min_length=2, max_length=255)


class CompanyUpdate(CompanyBase):
    # Sve opcionalno preko naslijeđenih polja (pydantic v2: koristimo partial update na ruti)
    pass


class CompanyOut(CompanyBase):
    id: int

    class Config:
        from_attributes = True
