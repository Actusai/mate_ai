from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_db
from app.models.company import Company
from app.models.package import Package

router = APIRouter()

@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
    return [
        {"id": r.id, "name": r.name, "email": r.email, "country": r.country,
         "is_authorized_representative": bool(getattr(r, "is_authorized_representative", 0))}
        for r in rows
    ]

@router.get("/packages")
def list_packages(
    db: Session = Depends(get_db),
    company_id: int | None = Query(default=None, description="Filter packages allowed for this company"),
):
    q = db.query(Package)
    packages = q.all()

    if company_id is not None:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            return []
        is_ar = bool(getattr(company, "is_authorized_representative", 0))
        if is_ar:
            # AR tvrtka: samo AR-only + ai_system_limit=0
            packages = [p for p in packages if bool(getattr(p, "is_ar_only", 0)) and int(getattr(p, "ai_system_limit", 0)) == 0]
        else:
            # ne-AR: sve osim AR-only
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
            "is_ar_only": bool(p.is_ar_only),
        }
        for p in packages
    ]
