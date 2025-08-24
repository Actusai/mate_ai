from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate

def get_company(db: Session, company_id: int) -> Optional[Company]:
    return db.query(Company).filter(Company.id == company_id).first()

def get_companies(db: Session, skip: int = 0, limit: int = 50) -> List[Company]:
    return db.query(Company).offset(skip).limit(limit).all()

def create_company(db: Session, payload: CompanyCreate) -> Company:
    obj = Company(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def update_company(db: Session, company: Company, payload: CompanyUpdate) -> Company:
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(company, k, v)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company

def delete_company(db: Session, company: Company) -> None:
    db.delete(company)
    db.commit()