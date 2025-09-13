# app/api/v1/dashboard.py
from typing import Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super,
    is_staff_admin,
    is_client_admin,
    is_contributor,
    get_assigned_company_ids,
    get_assigned_system_ids,
)
from app.models.user import User
from app.models.company import Company
from app.models.ai_system import AISystem
from app.models.system_assignment import SystemAssignment

router = APIRouter()

# ----- helpers -----

RISK_BUCKETS = (
    "prohibited",
    "high_risk",
    "limited_risk",
    "minimal_risk",
    "not_assessed",
)


def _bucket_for(risk_tier: str | None) -> str:
    """
    Normalizira risk_tier u jedan od RISK_BUCKETS.
    Sve što je None/prazno/'unknown'/'unassessed' -> 'not_assessed'.
    """
    if not risk_tier:
        return "not_assessed"
    v = risk_tier.strip().lower()
    if v in ("unknown", "unassessed", "not_assessed", ""):
        return "not_assessed"
    if v in ("prohibited", "high_risk", "limited_risk", "minimal_risk"):
        return v
    # fallback (neočekivane vrijednosti) -> not_assessed
    return "not_assessed"


def _empty_distribution() -> Dict[str, int]:
    return {k: 0 for k in RISK_BUCKETS}


# ----- endpoint: auto-scope summary -----


@router.get("/dashboard/summary")
def dashboard_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Vraća agregate i risk_distribution s bucketom 'not_assessed' umjesto 'unknown'.
    Auto-odabire scope prema ulozi:
      - super_admin: global
      - staff_admin: assigned companies
      - client_admin: own company
      - contributor: own assignments
    """
    dist = _empty_distribution()

    if is_super(current_user):
        scope = "global"
        companies_count = db.query(Company).count()
        ai_systems = db.query(AISystem).all()
        ai_systems_count = len(ai_systems)
        contributors_count = db.query(SystemAssignment.user_id).distinct().count()
        for s in ai_systems:
            dist[_bucket_for(s.risk_tier)] += 1
        return {
            "scope": scope,
            "companies_count": companies_count,
            "ai_systems_count": ai_systems_count,
            "contributors_count": contributors_count,
            "risk_distribution": dist,
        }

    if is_staff_admin(current_user):
        scope = "staff_admin"
        company_ids = get_assigned_company_ids(db, current_user.id)
        if not company_ids:
            return {
                "scope": scope,
                "companies_count": 0,
                "ai_systems_count": 0,
                "contributors_count": 0,
                "risk_distribution": dist,
            }
        companies_count = db.query(Company).filter(Company.id.in_(company_ids)).count()
        ai_systems = (
            db.query(AISystem).filter(AISystem.company_id.in_(company_ids)).all()
        )
        ai_systems_count = len(ai_systems)
        # unique contributors over those systems
        contributors_count = (
            db.query(SystemAssignment.user_id)
            .filter(SystemAssignment.ai_system_id.in_([s.id for s in ai_systems]))
            .distinct()
            .count()
        )
        for s in ai_systems:
            dist[_bucket_for(s.risk_tier)] += 1
        return {
            "scope": scope,
            "companies_count": companies_count,
            "ai_systems_count": ai_systems_count,
            "contributors_count": contributors_count,
            "risk_distribution": dist,
        }

    if is_client_admin(current_user):
        scope = "company"
        if not current_user.company_id:
            raise HTTPException(status_code=400, detail="User has no company assigned.")
        companies_count = 1
        ai_systems = (
            db.query(AISystem)
            .filter(AISystem.company_id == current_user.company_id)
            .all()
        )
        ai_systems_count = len(ai_systems)
        contributors_count = (
            db.query(SystemAssignment.user_id)
            .filter(SystemAssignment.ai_system_id.in_([s.id for s in ai_systems]))
            .distinct()
            .count()
        )
        for s in ai_systems:
            dist[_bucket_for(s.risk_tier)] += 1
        return {
            "scope": scope,
            "companies_count": companies_count,
            "ai_systems_count": ai_systems_count,
            "contributors_count": contributors_count,
            "risk_distribution": dist,
        }

    if is_contributor(current_user):
        scope = "contributor"
        assigned_system_ids = get_assigned_system_ids(db, current_user.id)
        if not assigned_system_ids:
            return {
                "scope": scope,
                "companies_count": 0,
                "ai_systems_count": 0,
                "contributors_count": 1,  # barem on sam :)
                "risk_distribution": dist,
            }
        ai_systems = (
            db.query(AISystem).filter(AISystem.id.in_(assigned_system_ids)).all()
        )
        for s in ai_systems:
            dist[_bucket_for(s.risk_tier)] += 1
        return {
            "scope": scope,
            "companies_count": len(set(s.company_id for s in ai_systems)),
            "ai_systems_count": len(ai_systems),
            "contributors_count": 1,  # fokus je na njegov portfelj
            "risk_distribution": dist,
        }

    # default (ako postoji neka neočekivana uloga)
    return {
        "scope": "unknown_role",
        "companies_count": 0,
        "ai_systems_count": 0,
        "contributors_count": 0,
        "risk_distribution": dist,
    }
