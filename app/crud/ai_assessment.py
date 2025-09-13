# app/crud/ai_assessment.py
from __future__ import annotations

from typing import Optional, Any, Dict, List
import json
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.ai_assessment import AIAssessment
from app.models.ai_system import AISystem
from app.schemas.ai_assessment import AIAssessmentCreate, AIAssessmentOut
from app.schemas.ai_system import RiskAssessmentAnswer
from app.services.risk_engine import classify_ai_system


# ------------------------
# Helpers
# ------------------------


def _to_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return json.dumps(str(obj), ensure_ascii=False)


def _from_json(s: Optional[str], fallback: Any) -> Any:
    if not s:
        return fallback
    try:
        return json.loads(s)
    except Exception:
        return fallback


def _normalize_obligations(obj: Any) -> Dict[str, List[str]]:
    """Always return {'core': [...], 'situational': [...]}."""
    if obj is None:
        return {"core": [], "situational": []}
    if isinstance(obj, dict):
        core = obj.get("core", [])
        situ = obj.get("situational", [])
        return {
            "core": [str(x) for x in core] if isinstance(core, list) else [],
            "situational": [str(x) for x in situ] if isinstance(situ, list) else [],
        }
    if isinstance(obj, list):
        return {"core": [str(x) for x in obj], "situational": []}
    return {"core": [str(obj)], "situational": []}


def _answers_to_dict(answers_obj: Any) -> Dict[str, Any]:
    if answers_obj is None:
        return {}
    if hasattr(answers_obj, "model_dump"):
        return answers_obj.model_dump(exclude_none=True)
    if isinstance(answers_obj, dict):
        return {k: v for k, v in answers_obj.items() if v is not None}
    return {}


def _answers_to_schema(answers_obj: Any) -> RiskAssessmentAnswer:
    """Map stored answers (JSON/dict) to RiskAssessmentAnswer schema."""
    if isinstance(answers_obj, str):
        answers_obj = _from_json(answers_obj, {})
    if not isinstance(answers_obj, dict):
        answers_obj = {}
    return RiskAssessmentAnswer.model_validate(answers_obj)


def _row_to_out(row: AIAssessment) -> AIAssessmentOut:
    answers = _answers_to_schema(row.answers_json or "{}")

    # Ako nema zasebnih kolona za rationale/references, koristi prazne liste
    rationale = _from_json(getattr(row, "rationale_json", None), [])
    references = _from_json(getattr(row, "references_json", None), [])
    obligations = _normalize_obligations(_from_json(row.obligations_json, {}))

    created_by = getattr(row, "created_by", None)
    created_at = getattr(row, "created_at", None) or datetime.utcnow()

    return AIAssessmentOut(
        id=row.id,
        system_id=row.ai_system_id,  # točan naziv stupca
        company_id=row.company_id,
        risk_tier=row.risk_tier or "minimal_risk",
        obligations=obligations,
        rationale=[str(x) for x in (rationale or [])],
        references=[str(x) for x in (references or [])],
        answers=answers,
        version_tag=getattr(row, "version_tag", None),
        created_by=int(created_by) if created_by is not None else 0,
        created_at=created_at,
    )


# ------------------------
# READ (latest / list / legacy single)
# ------------------------


def get_latest_for_system(db: Session, system_id: int) -> Optional[AIAssessment]:
    return (
        db.query(AIAssessment)
        .filter(AIAssessment.ai_system_id == system_id)
        .order_by(desc(AIAssessment.created_at), desc(AIAssessment.id))
        .first()
    )


def get_version(db: Session, system_id: int, version_id: int) -> Optional[AIAssessment]:
    """Dohvati određenu verziju procjene za AI sustav."""
    return (
        db.query(AIAssessment)
        .filter(
            AIAssessment.ai_system_id == system_id,
            AIAssessment.id == version_id,
        )
        .first()
    )


def list_versions_for_system(
    db: Session,
    system_id: int,
    skip: int = 0,
    limit: int = 50,
    sort_by: str = "created_at",  # supports: created_at | id
    order: str = "desc",  # asc | desc
) -> List[AIAssessment]:
    q = db.query(AIAssessment).filter(AIAssessment.ai_system_id == system_id)

    # Odabir polja za sortiranje (sigurna whitelista)
    if sort_by == "id":
        col = AIAssessment.id
    else:
        col = AIAssessment.created_at

    if order.lower() == "asc":
        q = q.order_by(col.asc(), AIAssessment.id.asc())
    else:
        q = q.order_by(col.desc(), AIAssessment.id.desc())

    return q.offset(skip).limit(limit).all()


# Back-compat: “single” assessment (stari endpoint ga je tako koristio)
def get_by_system(db: Session, system_id: int) -> Optional[AIAssessment]:
    return get_latest_for_system(db, system_id)


# ------------------------
# WRITE (versioned save) + legacy upsert
# ------------------------


def create_version_for_system(
    db: Session,
    system: AISystem,
    payload: AIAssessmentCreate,
    created_by: int,
) -> AIAssessment:
    """Kreira novu verziju procjene (bez brisanja starih)."""
    answers_dict = _answers_to_dict(payload.answers)
    result = classify_ai_system(answers_dict)

    risk_tier: str = result.get("risk_tier", "minimal_risk")
    obligations_obj = _normalize_obligations(result.get("obligations", {}))
    rationale = result.get("rationale", [])
    references = result.get("references", [])

    # Sastavi kwargs i dodaj opcionalne kolone samo ako postoje na modelu
    kwargs = dict(
        ai_system_id=system.id,
        company_id=system.company_id,
        answers_json=_to_json(answers_dict),
        risk_tier=risk_tier,
        prohibited=(risk_tier == "prohibited"),
        high_risk=(risk_tier == "high_risk"),
        obligations_json=_to_json(obligations_obj),
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    if hasattr(AIAssessment, "rationale_json"):
        kwargs["rationale_json"] = _to_json(rationale)
    if hasattr(AIAssessment, "references_json"):
        kwargs["references_json"] = _to_json(references)
    if hasattr(AIAssessment, "version_tag"):
        kwargs["version_tag"] = getattr(payload, "version_tag", None)

    row = AIAssessment(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# Alias zbog API importa (assessments.py očekuje upsert_version_for_system)
def upsert_version_for_system(
    db: Session,
    system: AISystem,
    payload: AIAssessmentCreate,
    created_by: int,
) -> AIAssessment:
    """Za sada 'upsert' radi kao 'create new version' (zadržavamo povijest)."""
    return create_version_for_system(db, system, payload, created_by)


def upsert_for_system(
    db: Session, system: AISystem, payload: AIAssessmentCreate
) -> AIAssessment:
    """
    Legacy: čuva točno jedan zapis po sustavu (update ako postoji, inače insert).
    Ostavili smo zbog kompatibilnosti starog endpointa.
    """
    answers_dict = _answers_to_dict(payload.answers)
    result = classify_ai_system(answers_dict)

    risk_tier: str = result.get("risk_tier", "minimal_risk")
    obligations_obj = _normalize_obligations(result.get("obligations", {}))
    rationale = result.get("rationale", [])
    references = result.get("references", [])

    row = get_latest_for_system(db, system.id)
    if not row:
        kwargs = dict(
            ai_system_id=system.id,
            company_id=system.company_id,
            answers_json=_to_json(answers_dict),
            risk_tier=risk_tier,
            prohibited=(risk_tier == "prohibited"),
            high_risk=(risk_tier == "high_risk"),
            obligations_json=_to_json(obligations_obj),
            created_by=None,  # legacy upsert možda nema info o korisniku
            created_at=datetime.utcnow(),
        )
        if hasattr(AIAssessment, "rationale_json"):
            kwargs["rationale_json"] = _to_json(rationale)
        if hasattr(AIAssessment, "references_json"):
            kwargs["references_json"] = _to_json(references)
        if hasattr(AIAssessment, "version_tag"):
            kwargs["version_tag"] = getattr(payload, "version_tag", None)

        row = AIAssessment(**kwargs)
        db.add(row)
    else:
        row.answers_json = _to_json(answers_dict)
        row.risk_tier = risk_tier
        row.prohibited = risk_tier == "prohibited"
        row.high_risk = risk_tier == "high_risk"
        row.obligations_json = _to_json(obligations_obj)
        if hasattr(row, "rationale_json"):
            row.rationale_json = _to_json(rationale)
        if hasattr(row, "references_json"):
            row.references_json = _to_json(references)
        if hasattr(row, "version_tag") and getattr(payload, "version_tag", None):
            row.version_tag = payload.version_tag

    db.commit()
    db.refresh(row)
    return row


# ------------------------
# OUT converters
# ------------------------


def to_out(row: AIAssessment) -> AIAssessmentOut:
    return _row_to_out(row)
