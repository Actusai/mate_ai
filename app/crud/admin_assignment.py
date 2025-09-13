# app/crud/admin_assignment.py
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.admin_assignment import AdminAssignment
from app.models.user import User
from app.models.company import Company

ALLOWED_ADMIN_ROLES = {"admin", "administrator_stranice", "site_admin"}


def list_all(db: Session) -> List[AdminAssignment]:
    return db.query(AdminAssignment).order_by(AdminAssignment.id.desc()).all()


def list_by_company(db: Session, company_id: int) -> List[AdminAssignment]:
    return (
        db.query(AdminAssignment)
        .filter(AdminAssignment.company_id == company_id)
        .order_by(AdminAssignment.id.desc())
        .all()
    )


def list_by_admin(db: Session, admin_user_id: int) -> List[AdminAssignment]:
    return (
        db.query(AdminAssignment)
        .filter(AdminAssignment.admin_id == admin_user_id)
        .order_by(AdminAssignment.id.desc())
        .all()
    )


def get(db: Session, assignment_id: int) -> Optional[AdminAssignment]:
    return db.query(AdminAssignment).filter(AdminAssignment.id == assignment_id).first()


def create(db: Session, admin_user_id: int, company_id: int) -> AdminAssignment:
    # validate admin user
    admin_user = db.query(User).filter(User.id == admin_user_id).first()
    if not admin_user:
        raise ValueError("Admin user not found")
    if (admin_user.role or "").lower() not in ALLOWED_ADMIN_ROLES:
        raise ValueError("User is not an admin/staff role")

    # validate company
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise ValueError("Company not found")

    # idempotent upsert-like: return existing if present
    existing = (
        db.query(AdminAssignment)
        .filter(
            AdminAssignment.admin_id == admin_user_id,
            AdminAssignment.company_id == company_id,
        )
        .first()
    )
    if existing:
        return existing

    obj = AdminAssignment(admin_id=admin_user_id, company_id=company_id)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, assignment: AdminAssignment) -> None:
    db.delete(assignment)
    db.commit()
