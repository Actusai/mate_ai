from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import is_super
from app.models.company import Company
from app.models.package import Package
from app.models.company_package import CompanyPackage
from pydantic import BaseModel, Field, conint

router = APIRouter()

class CompanyPackageAssign(BaseModel):
    company_id: conint(ge=1)
    package_id: conint(ge=1)

class CompanyPackageOut(BaseModel):
    company_id: int
    package_id: int

    class Config:
        from_attributes = True

@router.post("/company-packages", response_model=CompanyPackageOut, status_code=status.HTTP_201_CREATED)
def assign_package(payload: CompanyPackageAssign,
                   db: Session = Depends(get_db),
                   current_user = Depends(get_current_user)):
    if not is_super(current_user):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    company = db.query(Company).filter(Company.id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    package = db.query(Package).filter(Package.id == payload.package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    # upsert-style: jedna kompanija → jedan aktivni package zapis
    existing = db.query(CompanyPackage).filter(CompanyPackage.company_id == company.id).first()
    if existing:
        existing.package_id = package.id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return CompanyPackageOut(company_id=existing.company_id, package_id=existing.package_id)

    link = CompanyPackage(company_id=company.id, package_id=package.id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return CompanyPackageOut(company_id=link.company_id, package_id=link.package_id)