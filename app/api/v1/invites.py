from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.auth import get_db, get_current_user
from app.crud.invite import create_invite, accept_invite, get_invite_by_token
from app.schemas.invite import InviteCreate, InviteOut, InviteAccept
from app.models.user import User

router = APIRouter()


def _require_admin(user: User):
    if user.role not in ("admin", "super_admin", "administrator_stranice"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )


@router.post("/invites", response_model=InviteOut)
def api_create_invite(
    payload: InviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # samo admini/super-admini smiju slati pozivnice
    _require_admin(current_user)

    try:
        invite = create_invite(db, payload)
        return invite
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/invites/validate", response_model=InviteOut)
def api_validate_invite(
    token: str = Query(..., description="Invite token from email link"),
    db: Session = Depends(get_db),
):
    invite = get_invite_by_token(db, token)
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid token")

    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invite not pending")

    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invite expired")

    return invite


@router.post("/invites/accept")
def api_accept_invite(
    payload: InviteAccept,
    db: Session = Depends(get_db),
):
    try:
        user = accept_invite(db, token=payload.token, password=payload.password)
        return {"message": "Invite accepted. Account activated.", "email": user.email}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
