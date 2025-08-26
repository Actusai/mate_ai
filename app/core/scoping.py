# app/core/scoping.py
from typing import Any, Optional, List

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session, Query

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.models.admin_assignment import AdminAssignment
from app.models.ai_system import AISystem
from app.models.system_assignment import SystemAssignment

# ---- Role helpers ------------------------------------------------------------

SUPER_ROLES = {"super_admin"}
# legacy literals kept
STAFF_ADMIN_ROLES = {"administrator_stranice", "site_admin"}
CLIENT_ADMIN_ROLES = {"admin"}
CONTRIBUTOR_ROLES = {"member", "contributor"}  # treat "member" as contributor

def _role(user: Optional[User]) -> str:
    return (user.role or "").strip().lower() if user else ""

def is_super(user: User) -> bool:
    return _role(user) in SUPER_ROLES

def is_staff_admin(user: User) -> bool:
    return _role(user) in STAFF_ADMIN_ROLES

def is_client_admin(user: User) -> bool:
    return _role(user) in CLIENT_ADMIN_ROLES

def is_contributor(user: User) -> bool:
    return _role(user) in CONTRIBUTOR_ROLES

def is_admin(user: User) -> bool:
    """Back-compat: any admin-like role or super."""
    return is_super(user) or is_staff_admin(user) or is_client_admin(user)

# ---- Admin → Company assignment helpers -------------------------------------

def get_assigned_company_ids(db: Session, admin_user_id: int) -> List[int]:
    rows = (
        db.query(AdminAssignment.company_id)
        .filter(AdminAssignment.admin_id == admin_user_id)
        .all()
    )
    return [cid for (cid,) in rows]

def is_assigned_admin(db: Session, current_user: User, company_id: int) -> bool:
    if is_super(current_user):
        return True
    if not (is_staff_admin(current_user) or is_admin(current_user)):
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

# ---- Contributor → System assignment helpers --------------------------------

def get_assigned_system_ids(db: Session, user_id: int) -> List[int]:
    rows = (
        db.query(SystemAssignment.ai_system_id)
        .filter(SystemAssignment.user_id == user_id)
        .all()
    )
    return [sid for (sid,) in rows]

def is_assigned_contributor(db: Session, current_user: User, system_id: int) -> bool:
    if not is_contributor(current_user):
        return False
    return (
        db.query(SystemAssignment)
        .filter(
            SystemAssignment.user_id == current_user.id,
            SystemAssignment.ai_system_id == system_id,
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
    if is_super(current_user):
        return query
    if is_staff_admin(current_user) or is_client_admin(current_user):
        company_ids = get_assigned_company_ids(db, current_user.id) if is_staff_admin(current_user) else [current_user.company_id]
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

# ---- Company-level ACL (used in companies.py) --------------------------------

def can_read_company(db: Session, user: User, company_id: int) -> bool:
    if is_super(user):
        return True
    if user.company_id == company_id and not is_staff_admin(user):
        return True
    if is_staff_admin(user):
        return is_assigned_admin(db, user, company_id)
    return False

def can_write_company(db: Session, user: User, company_id: int) -> bool:
    if is_super(user):
        return True
    if is_client_admin(user) and user.company_id == company_id:
        return True
    if is_staff_admin(user):
        return is_assigned_admin(db, user, company_id)
    return False

# ---- System-level ACL (NEW) --------------------------------------------------

def can_read_system(db: Session, user: User, system: AISystem) -> bool:
    if is_super(user):
        return True
    # client admin → own company only
    if is_client_admin(user) and user.company_id == system.company_id:
        return True
    # staff admin → assigned to company
    if is_staff_admin(user) and is_assigned_admin(db, user, system.company_id):
        return True
    # contributor → must be assigned to the system
    if is_contributor(user) and is_assigned_contributor(db, user, system.id):
        return True
    # finally, non-admin non-contributor with same company? no.
    return False

def can_write_system_full(db: Session, user: User, system: AISystem) -> bool:
    """Full edit: super, client admin (own), staff admin (assigned)."""
    if is_super(user):
        return True
    if is_client_admin(user) and user.company_id == system.company_id:
        return True
    if is_staff_admin(user) and is_assigned_admin(db, user, system.company_id):
        return True
    return False

def can_write_system_limited(db: Session, user: User, system: AISystem) -> bool:
    """Limited edit: full editors OR assigned contributor."""
    if can_write_system_full(db, user, system):
        return True
    if is_contributor(user) and is_assigned_contributor(db, user, system.id):
        return True
    return False