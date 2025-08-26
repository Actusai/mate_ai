from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import can_read_company, can_write_company, is_super
from app.models.user import User
from app.models.ai_system import AISystem
from app.crud.ai_assessment import get_by_system, upsert_for_system, to_out
from app.schemas.ai_assessment import AIAssessmentCreate, AIAssessmentOut

router = APIRouter()

def _load_system_or_404(db: Session, system_id: int) -> AISystem:
    obj = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="AI system not found")
    return obj

@router.get("/ai-systems/{system_id}/assessment", response_model=AIAssessmentOut)
def get_assessment(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _load_system_or_404(db, system_id)
    if not can_read_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    row = get_by_system(db, system.id)
    if not row:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return to_out(row)

@router.post("/ai-systems/{system_id}/assessment", response_model=AIAssessmentOut, status_code=status.HTTP_201_CREATED)
def create_or_update_assessment(
    system_id: int,
    payload: AIAssessmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    system = _load_system_or_404(db, system_id)
    if not can_write_company(db, current_user, system.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    row = upsert_for_system(db, system, payload)
    return to_out(row)