# app/core/rbac.py
from __future__ import annotations
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.user import User
from app.crud.ai_system import get_system as crud_get_system
from app.core.scoping import (
    can_read_system as _can_read_system,
    can_write_system_full as _can_write_system_full,
    can_write_system_limited as _can_write_system_limited,
    # NEW: for strict company access we allow assigned staff admins
    is_assigned_admin,
)

# -----------------------------
# Basic checks
# -----------------------------


def is_super_admin(user: User) -> bool:
    return bool(getattr(user, "is_super_admin", False))


def ensure_superadmin(user: User) -> None:
    """Raise 403 if user is not Super Admin."""
    if not is_super_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin only"
        )


def ensure_same_company(user: User, company_id: int) -> None:
    """403 if user does not belong to this company (unless Super Admin)."""
    if is_super_admin(user):
        return
    if getattr(user, "company_id", None) != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (company)"
        )


def ensure_company_access(user: User, company_id: int) -> None:
    """
    Legacy/simple guard:
    Allow if Super Admin or the user belongs to the same company.
    """
    ensure_same_company(user, company_id)


def ensure_company_access_strict(
    user: User, company_id: int, db: Optional[Session] = None
) -> None:
    """
    Strict company access guard used by calendar & similar endpoints.

    Allows:
      - Super Admin,
      - users from the same company,
      - Staff Admin assigned to that client company (requires db).
    Otherwise → 403.
    """
    if is_super_admin(user):
        return
    if getattr(user, "company_id", None) == company_id:
        return
    if db is not None and is_assigned_admin(db, user, company_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges"
    )


# -----------------------------
# Membership helper (system level)
# -----------------------------


def _user_is_member_of_system(db: Session, user_id: int, ai_system_id: int) -> bool:
    """
    Is the user explicitly a member of the AI system?
      1) ai_system_members, or
      2) system_assignments (legacy)
    """
    # 1) ai_system_members
    row = db.execute(
        text(
            "SELECT 1 FROM ai_system_members WHERE ai_system_id=:aid AND user_id=:uid LIMIT 1"
        ),
        {"aid": ai_system_id, "uid": user_id},
    ).fetchone()
    if row:
        return True

    # 2) system_assignments
    row = db.execute(
        text(
            "SELECT 1 FROM system_assignments WHERE ai_system_id=:aid AND user_id=:uid LIMIT 1"
        ),
        {"aid": ai_system_id, "uid": user_id},
    ).fetchone()
    return bool(row)


# -----------------------------
# System-level guards
# -----------------------------


def ensure_system_access_read(db: Session, user: User, ai_system_id: int):
    """
    Allow read if:
      - Super Admin, or
      - scoping says user can read it, or
      - user is an explicit member of the system.
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found"
        )

    if is_super_admin(user):
        return system

    if _can_read_system(db, user, system):
        return system

    if _user_is_member_of_system(db, user.id, ai_system_id):
        return system

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (system read)"
    )


def ensure_system_write_full(db: Session, user: User, ai_system_id: int):
    """
    Allow full edits if:
      - Super Admin, or
      - scoping grants full write.
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found"
        )

    if is_super_admin(user):
        return system

    if not _can_write_system_full(db, user, system):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges"
        )
    return system


def ensure_system_write_limited(db: Session, user: User, ai_system_id: int):
    """
    Allow limited edits if:
      - Super Admin, or
      - scoping grants limited write.
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found"
        )

    if is_super_admin(user):
        return system

    if not _can_write_system_limited(db, user, system):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges"
        )
    return system


# -----------------------------
# Export guard (for /reports/export)
# -----------------------------


def ensure_member_filter_access(user: User, member_user_id: Optional[int]) -> None:
    """
    If filtering by member, allow:
      - Super Admin,
      - the same user (self),
      - company admin/owner/manager (per user.role).
    (Note: does not enforce same-company for the target member; use strict variant for that.)
    """
    if member_user_id is None:
        return
    if is_super_admin(user):
        return
    if member_user_id == getattr(user, "id", None):
        return
    role = (getattr(user, "role", "") or "").lower()
    if role in {"admin", "owner", "manager", "super_admin"}:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (member filter)"
    )


def ensure_member_filter_access_strict(
    db: Session, user: User, member_user_id: Optional[int]
) -> None:
    """
    Strict variant: same rules as ensure_member_filter_access, plus
    requires target member to be in the same company (unless Super Admin).
    """
    if member_user_id is None:
        return
    ensure_member_filter_access(user, member_user_id)
    if is_super_admin(user):
        return
    row = db.execute(
        text("SELECT 1 FROM users WHERE id = :uid AND company_id = :cid"),
        {"uid": member_user_id, "cid": getattr(user, "company_id", None)},
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Member not in your company"
        )


def ensure_export_access(db: Session, user: User, ai_system_id: Optional[int]) -> None:
    """If ai_system_id is provided, ensure the user can read that system."""
    if ai_system_id is None:
        return
    ensure_system_access_read(db, user, ai_system_id)
