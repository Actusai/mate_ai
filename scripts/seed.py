#!/usr/bin/env python3
"""
Minimal seed:
- Ensures a SuperAdmin user exists.
- Safe to run multiple times (idempotent).
"""
import os
import sys
from datetime import datetime

# enable 'app.' imports
sys.path.append(os.getcwd())

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.user import User
from app.core.security import get_password_hash  # must exist

def ensure_superadmin(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        # upgrade to super admin if needed
        changed = False
        if not getattr(user, "is_super_admin", False):
            user.is_super_admin = True
            changed = True
        if not getattr(user, "is_active", True):
            user.is_active = True
            changed = True
        if changed:
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    u = User(
        email=email,
        full_name="Super Admin",
        is_active=True,
        is_super_admin=True,
        role="super_admin",
        hashed_password=get_password_hash(password),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u

def main():
    email = os.environ.get("SEED_SUPERADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("SEED_SUPERADMIN_PASSWORD", "ChangeMe123!")

    db = SessionLocal()
    try:
        u = ensure_superadmin(db, email, password)
        print(f"OK: SuperAdmin ensured -> {u.email} (id={u.id})")
    finally:
        db.close()

if __name__ == "__main__":
    main()