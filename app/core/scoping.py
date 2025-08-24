# app/core/scoping.py
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session, Query

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.admin_assignment import AdminAssignment


# ---- Role helpers ------------------------------------------------------------

SUPER_ROLES = {"super_admin"}
# keep legacy literal "administrator_stranice"
ADMIN_ONLY_ROLES = {"admin", "administrator_stranice", "site_admin"}


def _role(user: Optional[User]) -> str:
    return (user.role or "").strip().lower() if user else ""


def is_super(user: User) -> bool:
    return _role(user) in SUPER_ROLES


def is_admin(user: User) -> bool:
    """Admins include super_admins."""
    return is_super(user) or _role(user) in ADMIN_ONLY_ROLES


# ---- Admin → Company assignment helpers -------------------------------------

def get_assigned_company_ids(db: Session, admin_user_id: int) -> list[int]:
    rows = (
        db.query(AdminAssignment.company_id)
        .filter(AdminAssignment.admin_id == admin_user_id)
        .all()
    )
    return [cid for (cid,) in rows]


def is_assigned_admin(db: Session, current_user: User, company_id: int) -> bool:
    """
    True if:
      - current_user is super_admin, OR
      - current_user is admin and has an explicit AdminAssignment(admin_id, company_id).
    """
    if is_super(current_user):
        return True
    if not is_admin(current_user):
        return False
    return (
        db.query(AdminAssignment)
        .filter(
            AdminAssignment.admin_id == current_user.id,
            AdminAssignment.company_id == company_id,
        )
        .first()
        is not None
    )


# ---- Hard guards (raise 403) -------------------------------------------------

def require_same_company_or_superadmin(
    target_company_id: int,
    current_user: User = Depends(get_current_user),
) -> None:
    if not is_super(current_user) and current_user.company_id != target_company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed for another company.",
        )


def require_admin_in_company(
    current_user: User = Depends(get_current_user),
) -> None:
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )


def require_admin_of_company_or_super(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if not is_assigned_admin(db, current_user, company_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin not assigned to this company.",
        )


# ---- Query scoping (soft helpers) -------------------------------------------

def scope_query_to_user_company(
    query: Query,
    current_user: User,
    company_field: Any,
) -> Query:
    if is_super(current_user):
        return query
    return query.filter(company_field == current_user.company_id)


def scope_query_to_admin_assignments(
    query: Query,
    db: Session,
    current_user: User,
    company_field: Any,
) -> Query:
    """
    - super_admin: no filter
    - admin (client or staff): filter to assigned company_ids
    - member: own company_id
    """
    if is_super(current_user):
        return query

    if is_admin(current_user):
        company_ids = get_assigned_company_ids(db, current_user.id)
        if not company_ids:
            return query.filter(False)
        return query.filter(company_field.in_(company_ids))

    return query.filter(company_field == current_user.company_id)


def ensure_resource_company_or_super(
    resource_company_id: int,
    current_user: User,
) -> None:
    if not is_super(current_user) and resource_company_id != current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource belongs to another company.",
        )


# ---- Single-company ACL helpers (needed by companies.py) ---------------------

def can_read_company(db: Session, user: User, company_id: int) -> bool:
    """super: yes; member: own; client admin: own; staff admin: assigned."""
    if is_super(user):
        return True
    if user.company_id == company_id:
        return True
    if is_admin(user):  # staff admins
        return (
            db.query(AdminAssignment)
            .filter(
                AdminAssignment.admin_id == user.id,
                AdminAssignment.company_id == company_id,
            )
            .first()
            is not None
        )
    return False


def can_write_company(db: Session, user: User, company_id: int) -> bool:
    """super: yes; client admin: own only; staff admin: assigned only; member: no."""
    if is_super(user):
        return True
    if _role(user) == "admin" and user.company_id == company_id:
        return True
    if is_admin(user) and _role(user) != "admin":
        return (
            db.query(AdminAssignment)
            .filter(
                AdminAssignment.admin_id == user.id,
                AdminAssignment.company_id == company_id,
            )
            .first()
            is not None
        )
    return False