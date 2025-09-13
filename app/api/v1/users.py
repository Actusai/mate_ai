# app/api/v1/users.py
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.core.rbac import ensure_company_access, is_super_admin
from app.services.audit import audit_log, ip_from_request

router = APIRouter()

# ---- helperi ----

# Što korisnik smije mijenjati nad SOBOM (ako nije admin)
SELF_UPDATE_ALLOWED = {
    "full_name",
    "email",
    # "role",         # uključi ako želiš dopustiti promjenu role običnim userima
    # "invite_status",
    # "is_active",
}

# Što admin/owner/manager/super_admin smije mijenjati nad userima svoje kompanije
ADMIN_UPDATE_ALLOWED = {
    "full_name",
    "email",
    "role",
    "invite_status",
    "is_active",
    # "company_id",  # po defaultu zabranjeno (dozvoli samo SuperAdminu u kodu ispod)
}

SENSITIVE_FORBIDDEN = {
    "hashed_password",
    "is_super_admin",
    "created_at",
    "updated_at",
    "last_login_at",
    "failed_login_attempts",
}

ROLE_SUPER = "super_admin"


def _role_allows_manage_company(user: User) -> bool:
    if is_super_admin(user):
        return True
    role = (getattr(user, "role", "") or "").lower()
    return role in {"admin", "owner", "manager", "super_admin"}


def _sanitize_user_out(u: User) -> Dict[str, Any]:
    # vrati bez osjetljivih polja
    return {
        "id": u.id,
        "email": u.email,
        "full_name": getattr(u, "full_name", None),
        "role": getattr(u, "role", None),
        "company_id": getattr(u, "company_id", None),
        "invite_status": getattr(u, "invite_status", None),
        "is_active": getattr(u, "is_active", True),
        "created_at": getattr(u, "created_at", None),
        "updated_at": getattr(u, "updated_at", None),
        "last_login_at": getattr(u, "last_login_at", None),
        "failed_login_attempts": getattr(u, "failed_login_attempts", 0),
        "is_super_admin": getattr(u, "is_super_admin", False),
    }


def _get_user_or_404(db: Session, user_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


def _ensure_actor_can_touch_target(current_user: User, target: User) -> None:
    """
    Dodatna zaštita: non-super ne smije dirati super_admin korisnika.
    """
    target_is_super = (
        (getattr(target, "role", "") or "").lower() == ROLE_SUPER
    ) or bool(getattr(target, "is_super_admin", False))
    if target_is_super and not is_super_admin(current_user):
        raise HTTPException(
            status_code=403, detail="Only SuperAdmin can modify a super_admin"
        )


# ---- ROUTES ----


@router.get("/users")
def list_users(
    company_id: Optional[int] = Query(
        None,
        description="Ako je SuperAdmin, može specificirati company_id; inače se ignorira",
    ),
    q: Optional[str] = Query(None, description="Pretraga po imenu ili emailu"),
    role: Optional[str] = Query(None, description="Filter po roli (admin/member/...)"),
    is_active: Optional[bool] = Query(
        None, description="Filter po aktivnim korisnicima"
    ),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    # Company scoping:
    if is_super_admin(current_user) and company_id is not None:
        cid = company_id
    else:
        cid = getattr(current_user, "company_id", None)
    if cid is None and not is_super_admin(current_user):
        raise HTTPException(status_code=403, detail="Company scope missing")

    qset = db.query(User)
    if not is_super_admin(current_user):
        qset = qset.filter(User.company_id == cid)
    else:
        if cid is not None:
            qset = qset.filter(User.company_id == cid)

    if q:
        like = f"%{q.lower()}%"
        qset = qset.filter(
            func.lower(User.email).like(like) | func.lower(User.full_name).like(like)
        )

    if role:
        qset = qset.filter(func.lower(User.role) == role.lower())

    if is_active is not None:
        qset = qset.filter(User.is_active == bool(is_active))

    users = (
        qset.order_by(User.full_name.asc(), User.email.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_sanitize_user_out(u) for u in users]


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    u = _get_user_or_404(db, user_id)

    # RBAC: isti company ili SuperAdmin
    if not is_super_admin(current_user):
        ensure_company_access(current_user, getattr(u, "company_id", None))

    return _sanitize_user_out(u)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: Dict[str, Any],  # možeš zamijeniti Pydanticom; za sada je fleksibilno
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    u = _get_user_or_404(db, user_id)

    # RBAC: isti company ili SuperAdmin
    if not is_super_admin(current_user):
        ensure_company_access(current_user, getattr(u, "company_id", None))
    _ensure_actor_can_touch_target(current_user, u)

    is_self = user_id == getattr(current_user, "id", None)
    can_manage = _role_allows_manage_company(current_user)

    # Odredi allowed polja
    if is_self and not can_manage:
        allowed_fields = set(SELF_UPDATE_ALLOWED)
    elif can_manage:
        allowed_fields = set(ADMIN_UPDATE_ALLOWED)
    else:
        allowed_fields = set()

    if not allowed_fields:
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    # Odbaci osjetljiva polja + sve što nije u allowlisti
    incoming = dict(payload or {})
    data: Dict[str, Any] = {
        k: v
        for k, v in incoming.items()
        if k in allowed_fields and k not in SENSITIVE_FORBIDDEN
    }

    # Dodatna pravila:
    # - promjena company_id samo SuperAdmin
    if "company_id" in data and not is_super_admin(current_user):
        raise HTTPException(
            status_code=403, detail="Only SuperAdmin can change company_id"
        )

    # - promjenu role dopuštamo samo onima koji mogu 'manage company'
    if "role" in data:
        if not can_manage:
            raise HTTPException(
                status_code=403, detail="Only admins can change user roles"
            )
        # postavljanje ili skidanje super_admin role dopušteno samo SuperAdminu
        if (
            data["role"]
            and str(data["role"]).lower() == ROLE_SUPER
            and not is_super_admin(current_user)
        ):
            raise HTTPException(
                status_code=403, detail="Only SuperAdmin can assign super_admin role"
            )
        if (getattr(u, "role", "") or "").lower() == ROLE_SUPER and not is_super_admin(
            current_user
        ):
            raise HTTPException(
                status_code=403, detail="Only SuperAdmin can modify a super_admin"
            )

    # Ako nema promjena, vrati korisnika
    if not data:
        return _sanitize_user_out(u)

    # Snapshot starih vrijednosti (za audit diff)
    old_snapshot = {k: getattr(u, k, None) for k in data.keys()}

    # Primijeni promjene
    for k, v in data.items():
        setattr(u, k, v)

    # Commit poslovne promjene
    db.add(u)
    db.commit()
    db.refresh(u)

    # AUDIT (best-effort; ne ruši poslovnu operaciju)
    try:
        audit_log(
            db,
            company_id=getattr(u, "company_id", None)
            or getattr(current_user, "company_id", 0),
            user_id=getattr(current_user, "id", None),
            action="USER_UPDATED",
            entity_type="user",
            entity_id=u.id,
            meta={
                "fields": list(data.keys()),
                "old": old_snapshot,
                "new": {k: getattr(u, k, None) for k in data.keys()},
                "acted_by": getattr(current_user, "id", None),
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _sanitize_user_out(u)


@router.post("/users/{user_id}/disable", response_model=Dict[str, Any])
def disable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    u = _get_user_or_404(db, user_id)

    # RBAC: isti company ili SuperAdmin + mora imati manage prava
    if not is_super_admin(current_user):
        ensure_company_access(current_user, getattr(u, "company_id", None))
        if not _role_allows_manage_company(current_user):
            raise HTTPException(status_code=403, detail="Insufficient privileges")
    _ensure_actor_can_touch_target(current_user, u)

    # Sigurnost: ne dopuštaj samodiskvalifikaciju non-super korisniku
    if user_id == getattr(current_user, "id", None) and not is_super_admin(
        current_user
    ):
        raise HTTPException(
            status_code=400, detail="You cannot disable your own account"
        )

    # Poslovna promjena
    u.is_active = False
    db.add(u)
    db.commit()
    db.refresh(u)

    # AUDIT
    try:
        audit_log(
            db,
            company_id=getattr(u, "company_id", None)
            or getattr(current_user, "company_id", 0),
            user_id=getattr(current_user, "id", None),
            action="USER_DISABLED",
            entity_type="user",
            entity_id=u.id,
            meta={"email": u.email},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _sanitize_user_out(u)


@router.post("/users/{user_id}/enable", response_model=Dict[str, Any])
def enable_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    u = _get_user_or_404(db, user_id)

    # RBAC: isti company ili SuperAdmin + manage prava
    if not is_super_admin(current_user):
        ensure_company_access(current_user, getattr(u, "company_id", None))
        if not _role_allows_manage_company(current_user):
            raise HTTPException(status_code=403, detail="Insufficient privileges")
    _ensure_actor_can_touch_target(current_user, u)

    u.is_active = True
    db.add(u)
    db.commit()
    db.refresh(u)

    # AUDIT
    try:
        audit_log(
            db,
            company_id=getattr(u, "company_id", None)
            or getattr(current_user, "company_id", 0),
            user_id=getattr(current_user, "id", None),
            action="USER_ENABLED",
            entity_type="user",
            entity_id=u.id,
            meta={"email": u.email},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _sanitize_user_out(u)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    u = _get_user_or_404(db, user_id)

    # RBAC: samo SuperAdmin ili company admin/owner/manager unutar iste tvrtke
    if not is_super_admin(current_user):
        ensure_company_access(current_user, getattr(u, "company_id", None))
        if not _role_allows_manage_company(current_user):
            raise HTTPException(status_code=403, detail="Insufficient privileges")
    _ensure_actor_can_touch_target(current_user, u)

    # Sigurnost: ne dopuštaj samobrisanje non-superu (po želji možeš i SuperAdminu zabraniti)
    if user_id == getattr(current_user, "id", None) and not is_super_admin(
        current_user
    ):
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account"
        )

    # Snapshot minimalnih podataka za audit prije brisanja
    meta_snapshot = {
        "email": getattr(u, "email", None),
        "full_name": getattr(u, "full_name", None),
        "role": getattr(u, "role", None),
        "company_id": getattr(u, "company_id", None),
        "invite_status": getattr(u, "invite_status", None),
        "is_active": getattr(u, "is_active", True),
    }

    # Poslovna operacija
    db.delete(u)
    db.commit()

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=meta_snapshot.get("company_id")
            or getattr(current_user, "company_id", 0),
            user_id=getattr(current_user, "id", None),
            action="USER_DELETED",
            entity_type="user",
            entity_id=user_id,
            meta=meta_snapshot,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
