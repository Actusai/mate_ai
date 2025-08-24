from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.password_reset import PasswordReset
from app.core.security import get_password_hash, verify_password


def _gen_token() -> str:
    return uuid4().hex


def create_reset_token(db: Session, email: str) -> str:
    """
    DEV ponašanje:
      - Ako user postoji: kreira token, sprema u DB i vraća token (da ga možeš testirati).
      - Ako user NE postoji: vrati prazan string (ne otkrivamo postoji li email).
    Production: uvijek vraća 200 bez tokena i šalje email.
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return ""

    token = _gen_token()
    pr = PasswordReset(
        user_id=user.id,
        token=token,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(minutes=30),
    )
    db.add(pr)
    db.commit()
    return token


def reset_password(db: Session, token: str, new_password: str) -> None:
    pr = db.query(PasswordReset).filter(PasswordReset.token == token).first()
    if not pr:
        raise ValueError("Invalid token")
    if pr.status != "pending":
        raise ValueError("Token not pending")
    if pr.expires_at and pr.expires_at < datetime.utcnow():
        pr.status = "expired"
        db.add(pr)
        db.commit()
        raise ValueError("Token expired")

    user = db.query(User).filter(User.id == pr.user_id).first()
    if not user:
        raise ValueError("User not found")

    user.hashed_password = get_password_hash(new_password)
    pr.status = "used"
    db.add(user)
    db.add(pr)
    db.commit()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise ValueError("Current password is incorrect")
    user.hashed_password = get_password_hash(new_password)
    db.add(user)
    db.commit()
