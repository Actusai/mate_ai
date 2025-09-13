# app/api/v1/auth.py
from datetime import timedelta, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.auth import authenticate_user, get_db
from app.core.security import create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from app.services.audit import audit_log, ip_from_request
from app.models.user import User

router = APIRouter()

# --- lockout settings ---
MAX_FAILED = 3
LOCK_MINUTES = 15


def _utcnow():
    # tz-aware UTC, ali pohranjujemo kao naive UTC (SQLite-friendly)
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/login")
def login(
    request: Request,  # ← mora ići prije parametara s defaultom
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    email = (form_data.username or "").strip()

    # 1) Dohvati usera po emailu (radi lockout checka)
    u = db.query(User).filter(func.lower(User.email) == email.lower()).first()

    # 2) Ako je zaključan i vrijeme nije isteklo → blokiraj
    if u and getattr(u, "locked_until", None):
        if _utcnow() < u.locked_until:
            try:
                audit_log(
                    db,
                    company_id=getattr(u, "company_id", 0) or 0,
                    user_id=getattr(u, "id", None),
                    action="LOGIN_BLOCKED_LOCKOUT",
                    entity_type="auth",
                    entity_id=getattr(u, "id", None),
                    meta={
                        "email": email,
                        "locked_until": u.locked_until.isoformat(),
                        "lock_minutes": LOCK_MINUTES,
                    },
                    ip=ip_from_request(request),
                )
                db.commit()
            except Exception:
                db.rollback()

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Your account is temporarily locked due to too many failed sign-in attempts. Please try again in {LOCK_MINUTES} minutes.",
            )

    # 3) Provjera lozinke
    user = authenticate_user(db, email, form_data.password)

    if not user:
        # Ako user postoji → povećaj promašaje / eventualno zaključaj
        if u:
            try:
                fails = (getattr(u, "failed_login_attempts", 0) or 0) + 1
                u.failed_login_attempts = fails

                if fails >= MAX_FAILED:
                    u.locked_until = _utcnow() + timedelta(minutes=LOCK_MINUTES)
                    u.failed_login_attempts = 0  # reset nakon locka

                    audit_log(
                        db,
                        company_id=getattr(u, "company_id", 0) or 0,
                        user_id=getattr(u, "id", None),
                        action="ACCOUNT_LOCKED",
                        entity_type="auth",
                        entity_id=getattr(u, "id", None),
                        meta={"email": email, "duration_min": LOCK_MINUTES},
                        ip=ip_from_request(request),
                    )
                else:
                    audit_log(
                        db,
                        company_id=getattr(u, "company_id", 0) or 0,
                        user_id=getattr(u, "id", None),
                        action="LOGIN_FAILED",
                        entity_type="auth",
                        entity_id=None,
                        meta={"email": email, "failed_attempts": fails},
                        ip=ip_from_request(request),
                    )
                db.add(u)
                db.commit()
            except Exception:
                db.rollback()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4) Uspješna prijava → reset failova/locka, update last_login_at
    try:
        if hasattr(user, "failed_login_attempts"):
            user.failed_login_attempts = 0
        if hasattr(user, "locked_until"):
            user.locked_until = None
        if hasattr(user, "last_login_at"):
            user.last_login_at = datetime.utcnow()
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()

    # audit success
    try:
        audit_log(
            db,
            company_id=getattr(user, "company_id", 0) or 0,
            user_id=getattr(user, "id", None),
            action="LOGIN_SUCCESS",
            entity_type="auth",
            entity_id=getattr(user, "id", None),
            meta={"email": getattr(user, "email", None), "method": "password"},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}
