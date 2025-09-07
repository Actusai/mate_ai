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
)

# -----------------------------
# Osnovni checkovi
# -----------------------------

def is_super_admin(user: User) -> bool:
    return bool(getattr(user, "is_super_admin", False))

def ensure_superadmin(user: User) -> None:
    """Podigni 403 ako korisnik nije super admin."""
    if not is_super_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin only")

def ensure_same_company(user: User, company_id: int) -> None:
    """403 ako user ne pripada toj kompaniji (osim ako je superadmin)."""
    if is_super_admin(user):
        return
    if getattr(user, "company_id", None) != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (company)")

def ensure_company_access(user: User, company_id: int) -> None:
    """Dozvoli ako je superadmin ili user pripada toj kompaniji."""
    ensure_same_company(user, company_id)

# -----------------------------
# Membership helper (system level)
# -----------------------------

def _user_is_member_of_system(db: Session, user_id: int, ai_system_id: int) -> bool:
    """
    Provjerava je li user eksplicitno član AI sustava:
      1) ai_system_members (naša tablica), ili
      2) system_assignments (legacy/postojeća tablica)
    """
    # 1) ai_system_members
    row = db.execute(
        text("SELECT 1 FROM ai_system_members WHERE ai_system_id=:aid AND user_id=:uid LIMIT 1"),
        {"aid": ai_system_id, "uid": user_id},
    ).fetchone()
    if row:
        return True

    # 2) system_assignments
    row = db.execute(
        text("SELECT 1 FROM system_assignments WHERE ai_system_id=:aid AND user_id=:uid LIMIT 1"),
        {"aid": ai_system_id, "uid": user_id},
    ).fetchone()
    return bool(row)

# -----------------------------
# System-level checkovi
# -----------------------------

def ensure_system_access_read(db: Session, user: User, ai_system_id: int):
    """
    Dozvoli čitanje ako:
      - superadmin, ili
      - _can_read_system (scoping; pokriva i cross-company dodjele), ili
      - user je eksplicitni član sustava (ai_system_members/system_assignments).
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found")

    if is_super_admin(user):
        return system

    # prvo scoping pravila (dopuštaju i cross-company scenarije)
    if _can_read_system(db, user, system):
        return system

    # fallback: eksplicitno članstvo (također može biti cross-company)
    if _user_is_member_of_system(db, user.id, ai_system_id):
        return system

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (system read)")

def ensure_system_write_full(db: Session, user: User, ai_system_id: int):
    """
    Dozvoli pune izmjene ako:
      - superadmin, ili
      - _can_write_system_full (scoping; može biti cross-company).
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found")

    if is_super_admin(user):
        return system

    if not _can_write_system_full(db, user, system):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges")
    return system

def ensure_system_write_limited(db: Session, user: User, ai_system_id: int):
    """
    Dozvoli ograničene izmjene ako:
      - superadmin, ili
      - _can_write_system_limited (scoping; može biti cross-company).
    """
    system = crud_get_system(db, ai_system_id)
    if not system:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI system not found")

    if is_super_admin(user):
        return system

    if not _can_write_system_limited(db, user, system):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges")
    return system

# -----------------------------
# Export guard (za /reports/export)
# -----------------------------

def ensure_member_filter_access(user: User, member_user_id: Optional[int]) -> None:
    """
    Ako se traži filter po članu, dozvoli:
      - superadmin,
      - isti korisnik (self),
      - kompanijski admin/owner/manager (prema user.role).
    (NAPOMENA: ne provjerava company match target membera; za to koristi strict varijantu.)
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
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden (member filter)")

def ensure_member_filter_access_strict(db: Session, user: User, member_user_id: Optional[int]) -> None:
    """
    Striktnija varijanta: uz pravila iz ensure_member_filter_access, dodatno zahtijeva
    da je target member iz iste kompanije kao i requester (ako requester nije superadmin).
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member not in your company")

def ensure_export_access(db: Session, user: User, ai_system_id: Optional[int]) -> None:
    """Ako je naveden ai_system_id, provjeri da user smije čitati taj sustav."""
    if ai_system_id is None:
        return
    ensure_system_access_read(db, user, ai_system_id)