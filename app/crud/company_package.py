from sqlalchemy.orm import Session
from sqlalchemy import exists, and_
from app.models.company import Company
from app.models.package import Package
from app.models.company_package import (
    CompanyPackage,
)  # pretpostavka: model postoji (company_packages)
from app.schemas.company_package import CompanyPackageAssign


def assign_package(db: Session, payload: CompanyPackageAssign) -> CompanyPackage:
    # validacije
    if not db.query(exists().where(Company.id == payload.company_id)).scalar():
        raise ValueError("Company not found")
    if not db.query(exists().where(Package.id == payload.package_id)).scalar():
        raise ValueError("Package not found")

    # Ako želiš “jedan aktivni paket po kompaniji”, možeš prethodne deaktivirati ili obrisati
    # Ovdje jednostavno stvaramo novi zapis.
    obj = CompanyPackage(
        company_id=payload.company_id,
        package_id=payload.package_id,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
