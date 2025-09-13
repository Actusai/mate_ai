# app/api/v1/invites.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.auth import get_db, get_current_user
from app.core.scoping import require_admin_in_company, is_super
from app.crud.invite import create_invite, accept_invite, get_invite_by_token
from app.schemas.invite import InviteCreate, InviteOut, InviteAccept
from app.models.user import User
from app.services.audit import audit_log, ip_from_request  # AUDIT

router = APIRouter()


@router.post("/invites", response_model=InviteOut)
def api_create_invite(
    payload: InviteCreate,
    request: Request,  # ⬅️ bez defaulta i ispred Depends parametara
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_admin_in_company),  # must be admin/super
):
    # Non-super može pozivati samo unutar vlastite tvrtke
    if not is_super(current_user) and payload.company_id != current_user.company_id:
        raise HTTPException(
            status_code=403, detail="Cannot invite for another company."
        )

    try:
        invite = create_invite(db, payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # --- AUDIT (best-effort) ---
    try:
        audit_log(
            db,
            company_id=payload.company_id,
            user_id=getattr(current_user, "id", None),
            action="INVITE_CREATED",
            entity_type="invite",
            entity_id=getattr(invite, "id", None),
            meta={
                "email": getattr(invite, "email", getattr(payload, "email", None)),
                "role": getattr(invite, "role", getattr(payload, "role", None)),
                "invited_by": getattr(current_user, "id", None),
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return invite


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
    request: Request,  # ⬅️ bez defaulta i ispred Depends parametara
    db: Session = Depends(get_db),
):
    # Uzmi invite prije accept-a radi audita
    invite = get_invite_by_token(db, payload.token)
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid token")

    try:
        user = accept_invite(db, token=payload.token, password=payload.password)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # --- AUDIT (best-effort) ---
    try:
        audit_log(
            db,
            company_id=getattr(invite, "company_id", 0),
            user_id=getattr(user, "id", None),  # korisnik koji je upravo aktiviran
            action="INVITE_ACCEPTED",
            entity_type="invite",
            entity_id=getattr(invite, "id", None),
            meta={
                "email": getattr(invite, "email", None),
                "accepted_user_email": getattr(user, "email", None),
                "token_suffix": (payload.token[-6:] if payload.token else None),
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"message": "Invite accepted. Account activated.", "email": user.email}
