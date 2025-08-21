from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_db
from app.models.company import Company
from app.models.package import Package

router = APIRouter()

@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
    return [
        {"id": r.id, "name": r.name, "email": r.email, "country": r.country}
        for r in rows
    ]

@router.get("/packages")
def list_packages(db: Session = Depends(get_db)):
    rows = db.query(Package).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "price": r.price,
            "ai_system_limit": r.ai_system_limit,
            "user_limit": r.user_limit,
        }
        for r in rows
    ]
