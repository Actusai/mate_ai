from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.auth import get_db, get_current_user
from app.core.scoping import require_admin_in_company, is_super
from app.crud.invite import create_invite, accept_invite, get_invite_by_token
from app.schemas.invite import InviteCreate, InviteOut, InviteAccept
from app.models.user import User

router = APIRouter()

@router.post("/invites", response_model=InviteOut)
def api_create_invite(
    payload: InviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_admin_in_company),  # must be admin/super
):
    # Non-super can invite only within own company
    if not is_super(current_user) and payload.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot invite for another company.")

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
