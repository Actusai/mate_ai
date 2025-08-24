from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.crud.passwords import change_password
from app.schemas.passwords import ChangePasswordRequest
from app.models.user import User

router = APIRouter()

@router.post("/auth/change-password")
def api_change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    PROTECTED endpoint – treba Bearer token korisnika koji mijenja lozinku.
    """
    try:
        change_password(
            db,
            user=current_user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        return {"msg": "Password changed"}
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
