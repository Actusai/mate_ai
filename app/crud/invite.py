from datetime import datetime, timedelta
from uuid import uuid4
from typing import Optional

from sqlalchemy.orm import Session

from app.models.invite import Invite
from app.models.user import User
from app.models.company import Company
from app.models.package import Package
from app.core.security import get_password_hash
from app.schemas.invite import InviteCreate

STAFF_ROLES = {"administrator_stranice", "site_admin", "super_admin"}
CLIENT_ROLES = {"admin", "member"}  # proširi po potrebi


def _generate_token() -> str:
    return uuid4().hex


def _get_company(db: Session, company_id: Optional[int]) -> Optional[Company]:
    if company_id is None:
        return None
    return db.query(Company).filter(Company.id == company_id).first()


def _get_package(db: Session, package_id: Optional[int]) -> Optional[Package]:
    if package_id is None:
        return None
    return db.query(Package).filter(Package.id == package_id).first()


def create_invite(db: Session, payload: InviteCreate) -> Invite:
    """
    Creates an invite.
    Business rules:
      - Staff roles (administrator_stranice/site_admin/super_admin): SKIP AR/package checks.
      - Client roles (admin/member): (optionally) validate company/package if you want now,
        but prefer to enforce AI system limits later during AI system CRUD, not here.
    """
    token = _generate_token()
    expires_at = datetime.utcnow() + timedelta(days=payload.expires_in_days)

    # Basic existence checks
    company: Optional[Company] = _get_company(db, payload.company_id)
    if payload.company_id is not None and not company:
        raise ValueError("Company not found")

    pkg: Optional[Package] = _get_package(db, payload.package_id)
    if payload.package_id is not None and not pkg:
        raise ValueError("Package not found")

    role_l = (payload.role or "").strip().lower()

    # --- IMPORTANT CHANGE: do not enforce AR-specific package rules for staff roles ---
    if role_l in CLIENT_ROLES:
        # (Optional) place minimal validation for client roles if you really want):
        # Example: ensure company exists for client roles
        if company is None:
            raise ValueError("Client users must be tied to a company")
        # You COULD also check whether the chosen package is allowed for that company,
        # but we recommend enforcing hard limits (AI system count, etc.) at AI-system CRUD time.

    # Build and persist invite
    invite = Invite(
        email=payload.email,
        token=token,
        company_id=payload.company_id,  # may be None for staff if you later relax schema
        package_id=payload.package_id,  # optional; not enforced for staff
        role=payload.role,
        status="pending",
        expires_at=expires_at,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def get_invite_by_token(db: Session, token: str) -> Invite | None:
    return db.query(Invite).filter(Invite.token == token).first()


def accept_invite(db: Session, token: str, password: str) -> User:
    invite = get_invite_by_token(db, token)
    if not invite:
        raise ValueError("Invalid token")

    if invite.status != "pending":
        raise ValueError("Invite not pending")

    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise ValueError("Invite expired")

    # Check if user already exists
    existing = db.query(User).filter(User.email == invite.email).first()
    if existing:
        raise ValueError("User with this email already exists")

    role_l = (invite.role or "").strip().lower()

    # For staff roles we allow company_id to be None (multi-tenant via assignments).
    # If your schema requires company_id, you can keep it but it will be ignored by scoping for staff.
    user = User(
        email=invite.email,
        hashed_password=get_password_hash(password),
        company_id=invite.company_id if role_l in CLIENT_ROLES else None,
        role=invite.role,
    )
    db.add(user)

    invite.status = "accepted"
    db.add(invite)

    db.commit()
    db.refresh(user)
    return user
