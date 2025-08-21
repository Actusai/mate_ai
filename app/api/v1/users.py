from fastapi import APIRouter, Depends
from app.core.auth import get_current_user
from app.models.user import User

router = APIRouter()

@router.get("/me")
def read_me(current_user: User = Depends(get_current_user)):
    return {
        "email": current_user.email,
        "role": current_user.role,
        "company_id": current_user.company_id,
    }
