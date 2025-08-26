from typing import Optional
from sqlalchemy.orm import Session
from app.models.ai_assessment import AIAssessment
from app.models.ai_system import AISystem
from app.schemas.ai_assessment import AIAssessmentCreate, AIAssessmentOut
from app.services.risk_engine import (
    evaluate_answers, serialize_answers, serialize_obligations, deserialize_answers,
)

def get_by_system(db: Session, system_id: int) -> Optional[AIAssessment]:
    return db.query(AIAssessment).filter(AIAssessment.system_id == system_id).first()

def upsert_for_system(db: Session, system: AISystem, payload: AIAssessmentCreate) -> AIAssessment:
    risk_tier, prohibited, high_risk, obligations = evaluate_answers(payload.answers)
    row = get_by_system(db, system.id)
    if not row:
        row = AIAssessment(
            system_id=system.id,
            company_id=system.company_id,
            answers_json=serialize_answers(payload.answers),
            risk_tier=risk_tier,
            prohibited=prohibited,
            high_risk=high_risk,
            obligations_json=serialize_obligations(obligations),
        )
        db.add(row)
    else:
        row.answers_json = serialize_answers(payload.answers)
        row.risk_tier = risk_tier
        row.prohibited = prohibited
        row.high_risk = high_risk
        row.obligations_json = serialize_obligations(obligations)
    db.commit()
    db.refresh(row)
    return row

def to_out(row: AIAssessment) -> AIAssessmentOut:
    return AIAssessmentOut(
        id=row.id,
        system_id=row.system_id,
        company_id=row.company_id,
        answers=deserialize_answers(row.answers_json),
        risk_tier=row.risk_tier,
        prohibited=row.prohibited,
        high_risk=row.high_risk,
        obligations=__import__("json").loads(row.obligations_json or "[]"),
    )