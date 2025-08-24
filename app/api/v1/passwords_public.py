from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db
from app.crud.passwords import create_reset_token, reset_password
from app.schemas.passwords import ForgotPasswordRequest, ResetPasswordRequest

router = APIRouter()

@router.post("/auth/forgot-password")
def api_forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    PUBLIC endpoint – bez autentikacije.
    DEV: vraća 'dev_token' ako user postoji (za test bez slanja e‑maila).
    """
    try:
        token = create_reset_token(db, payload.email)
        if token:
            return {"msg": "Password reset token generated", "dev_token": token}
        else:
            return {"msg": "If that email exists, reset instructions were sent"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/auth/reset-password")
def api_reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    PUBLIC endpoint – bez autentikacije.
    """
    try:
        reset_password(db, payload.token, payload.new_password)
        return {"msg": "Password has been reset successfully"}
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
