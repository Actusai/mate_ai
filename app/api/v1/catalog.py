# app/api/v1/catalog.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_db
from app.models.company import Company
from app.models.package import Package  # ✅

# ➜ Pomakni cijeli katalog pod vlastiti pod-prefix i tag
router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/companies", operation_id="catalog_list_companies")
def list_companies_catalog(db: Session = Depends(get_db)):
    rows = db.query(Company).order_by(Company.id.desc()).all()
    out = []
    for r in rows:
        is_ar_flag = bool(getattr(r, "is_authorized_representative", 0))
        company_type = getattr(r, "company_type", None)
        is_ar_type = (company_type or "").lower() == "authorized_representative"
        is_ar = is_ar_flag or is_ar_type
        out.append(
            {
                "id": r.id,
                "name": r.name,
                "address": getattr(r, "address", None),
                "country": getattr(r, "country", None),
                "legal_form": getattr(r, "legal_form", None),
                "registration_number": getattr(r, "registration_number", None),
                "website": getattr(r, "website", None),
                "contact_email": getattr(r, "contact_email", None),
                "contact_phone": getattr(r, "contact_phone", None),
                "contact_person": getattr(r, "contact_person", None),
                "company_type": company_type,
                "is_authorized_representative": is_ar,
            }
        )
    return out


@router.get("/packages", operation_id="catalog_list_packages")
def list_packages_catalog(
    db: Session = Depends(get_db),
    company_id: int | None = Query(
        default=None, description="Filtriraj pakete za tvrtku (prema company_type/AR)."
    ),
    company_type: str | None = Query(
        default=None,
        description="'authorized_representative' | 'deployer' | 'developer'",
    ),
):
    packages = db.query(Package).order_by(Package.id.desc()).all()

    effective_company_type = None
    if company_id is not None:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            return []
        is_ar_flag = bool(getattr(company, "is_authorized_representative", 0))
        company_type_db = (getattr(company, "company_type", None) or "").lower()
        is_ar_type = company_type_db == "authorized_representative"
        effective_company_type = (
            "authorized_representative"
            if (is_ar_flag or is_ar_type)
            else (company_type_db or "deployer")
        )

    if company_type:
        effective_company_type = company_type.lower()

    if effective_company_type:
        if effective_company_type == "authorized_representative":
            packages = [
                p
                for p in packages
                if bool(getattr(p, "is_ar_only", 0))
                and int(getattr(p, "ai_system_limit", 0) or 0) == 0
            ]
        else:
            packages = [p for p in packages if not bool(getattr(p, "is_ar_only", 0))]

    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "price": p.price,
            "ai_system_limit": p.ai_system_limit,
            "user_limit": p.user_limit,
            "client_limit": p.client_limit,
            "is_ar_only": bool(getattr(p, "is_ar_only", 0)),
        }
        for p in packages
    ]
