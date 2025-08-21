from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy.orm import Session

from app.models.invite import Invite
from app.models.user import User
from app.models.company import Company
from app.models.package import Package
from app.core.security import get_password_hash
from app.schemas.invite import InviteCreate


def _generate_token() -> str:
    return uuid4().hex


def create_invite(db: Session, payload: InviteCreate) -> Invite:
    """
    Create an invite for a user. Strict validation:
    - company_id and package_id must be >= 1
    - Company and Package must exist
    """
    if payload.company_id < 1:
        raise ValueError("company_id must be >= 1")
    if payload.package_id < 1:
        raise ValueError("package_id must be >= 1")

    if not db.query(Company).filter(Company.id == payload.company_id).first():
        raise ValueError("Company not found")

    if not db.query(Package).filter(Package.id == payload.package_id).first():
        raise ValueError("Package not found")

    token = _generate_token()
    expires_at = datetime.utcnow() + timedelta(days=payload.expires_in_days)

    invite = Invite(
        email=payload.email,
        token=token,
        company_id=payload.company_id,
        package_id=payload.package_id,
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
    """
    Accept an invite:
    - token must exist, be pending, and not expired
    - email must not already have a user
    - creates the user with provided password, marks invite accepted
    """
    invite = get_invite_by_token(db, token)
    if not invite:
        raise ValueError("Invalid token")

    if invite.status != "pending":
        raise ValueError("Invite not pending")

    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise ValueError("Invite expired")

    # extra safety: ensure company/package still exist
    if not db.query(Company).filter(Company.id == invite.company_id).first():
        raise ValueError("Company not found")
    if not db.query(Package).filter(Package.id == invite.package_id).first():
        raise ValueError("Package not found")

    existing = db.query(User).filter(User.email == invite.email).first()
    if existing:
        raise ValueError("User with this email already exists")

    user = User(
        email=invite.email,
        hashed_password=get_password_hash(password),
        company_id=invite.company_id,
        role=invite.role,
    )
    db.add(user)

    invite.status = "accepted"
    db.add(invite)

    db.commit()
    db.refresh(user)
    return user
