# app/core/auth.py
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.user import User
from app.core.security import verify_password, SECRET_KEY, ALGORITHM

# OAuth2 bearer scheme for Swagger "Authorize" button and DI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")

# Lockout policy (tweak as needed)
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 15


def get_db():
    """Yield a DB session and make sure it's closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _is_locked(user: User, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    return bool(getattr(user, "locked_until", None) and user.locked_until > now)


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    Attempt authentication:
      - if user doesn't exist → return None (do not reveal existence)
      - if user is deactivated → 403
      - if account is locked → 423
      - if password is correct → reset counters and return user
      - if password is incorrect → increment counter, lock if needed, return None
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # Do not reveal whether the user exists
        return None

    # Deactivated
    if getattr(user, "is_active", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled"
        )

    # Already locked?
    if _is_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked until {user.locked_until.isoformat()}",
        )

    # Password check
    if verify_password(password, user.hashed_password):
        # Success → reset counters and lock (if any)
        if getattr(user, "failed_login_attempts", 0) or getattr(
            user, "locked_until", None
        ):
            user.failed_login_attempts = 0
            user.locked_until = None
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    # Failure → increment counter and potentially lock
    user.failed_login_attempts = int(getattr(user, "failed_login_attempts", 0) or 0) + 1

    if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
        user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        # Optionally reset the counter after locking:
        # user.failed_login_attempts = 0

    db.add(user)
    db.commit()
    # Do not reveal whether the account is locked/wrong password → login route can return 401
    return None


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Decode JWT and load the user from DB or return 401.
    Additionally:
      - reject deactivated users (403)
      - reject currently locked users (403)
      - **side-effect**: store user context on request.state (for request logging)
        - request.state.user_id
        - request.state.company_id
        - request.state.is_super_admin
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: Optional[str] = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    # Deactivated?
    if getattr(user, "is_active", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled"
        )

    # Locked? (by design you could allow existing token; here we reject)
    if _is_locked(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account locked until {user.locked_until.isoformat()}",
        )

    # Expose user context to middleware/loggers
    if hasattr(request, "state"):
        try:
            request.state.user_id = getattr(user, "id", None)
            request.state.company_id = getattr(user, "company_id", None)
            request.state.is_super_admin = bool(getattr(user, "is_super_admin", False))
        except Exception:
            # Never break auth flow because of logging context
            pass

    return user
