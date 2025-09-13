# app/api/v1/systems.py
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import text, func, bindparam  # func used for optional task handover

from app.core.auth import get_db, get_current_user
from app.core.scoping import (
    is_super,
    is_admin,
    is_contributor,
    can_write_company,
    get_assigned_company_ids,
    get_assigned_system_ids,
)
from app.models.user import User
from app.models.ai_system import AISystem
from app.models.system_assignment import SystemAssignment
from app.schemas.ai_system import (
    AISystemCreate,
    AISystemUpdate,
    AISystemOut,
    AISystemOutExtended,
    RiskAssessmentAnswer,
    RiskAssessmentResult,
)
from app.schemas.system_ar import (
    AssignARRequest,
    BulkTransferRequest,
    BulkTransferResult,
    SkippedItem,
)

from app.core.rbac import (
    ensure_company_access,
    ensure_system_access_read,
    ensure_system_write_full,
    ensure_system_write_limited,
)

from app.crud.ai_system import (
    get_system as crud_get_system,
    get_all_systems as crud_get_all_systems,
    get_systems_by_company_ids as crud_get_systems_by_company_ids,
    create_system as crud_create_system,
    update_system as crud_update_system,
    delete_system as crud_delete_system,
)

from app.services.risk_engine import classify_ai_system
from app.services.audit import audit_log, ip_from_request

# Compliance/effective risk badges
from app.services.reporting import compliance_status_from_pct, compute_effective_risk

# Optional notifications (best-effort; safe if missing)
try:
    from app.services.notifications import produce_ar_assigned, produce_ar_unassigned  # type: ignore
except Exception:  # pragma: no cover
    produce_ar_assigned = None  # type: ignore
    produce_ar_unassigned = None  # type: ignore

# OPTIONAL: tasks model (used for AR handover reassignment)
try:
    from app.models.compliance_task import ComplianceTask  # pragma: no cover
except Exception:  # pragma: no cover
    ComplianceTask = None  # type: ignore

router = APIRouter()

# Contributor can only update these fields
CONTRIBUTOR_ALLOWED_FIELDS = {"notes", "status", "lifecycle_stage"}


# ---------------------------
# Helpers
# ---------------------------
def _to_out(s: AISystem) -> AISystemOut:
    # Note: list endpoints return AISystemOut without AR expansion (to avoid extra queries).
    return AISystemOut.model_validate(s)


def _fetch_system_compliance(db: Session, system_id: int) -> Dict[str, Any]:
    """
    Pull compliance_pct and overdue_cnt from vw_system_compliance.
    If no row exists, default to 100% compliant and 0 overdue.
    """
    row = (
        db.execute(
            text(
                """
            SELECT compliance_pct, overdue_cnt
            FROM vw_system_compliance
            WHERE ai_system_id = :aid
            LIMIT 1
            """
            ),
            {"aid": system_id},
        )
        .mappings()
        .first()
    )

    if row:
        pct = float(row.get("compliance_pct") or 0.0)
        overdue = int(row.get("overdue_cnt") or 0)
    else:
        pct, overdue = 100.0, 0

    cs = compliance_status_from_pct(pct, overdue)
    return {"compliance_pct": pct, "overdue_cnt": overdue, "compliance_status": cs}


def get_system_or_404(db: Session, system_id: int) -> AISystem:
    s = db.query(AISystem).filter(AISystem.id == system_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="AI system not found")
    return s


def validate_user_in_company(db: Session, user_id: int, company_id: int) -> User:
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if getattr(u, "company_id", None) != company_id:
        raise HTTPException(
            status_code=400, detail="User belongs to a different company"
        )
    if getattr(u, "is_active", True) is False:
        raise HTTPException(status_code=400, detail="User is not active")
    return u


def unset_ar_assignment(db: Session, system_id: int) -> int:
    """
    Remove any existing Authorized Representative assignment for the system.
    Returns the number of rows deleted.
    """
    q = db.query(SystemAssignment).filter(
        SystemAssignment.ai_system_id == system_id,
        SystemAssignment.role == "authorized_representative",
    )
    deleted = q.delete(synchronize_session=False)
    return int(deleted)


def _fetch_current_ar(db: Session, system_id: int) -> Optional[Dict[str, Any]]:
    """
    Return {'user_id': int, 'email': str|None} for current AR on this system, or None if not set.
    """
    row = (
        db.execute(
            text(
                """
            SELECT u.id AS user_id, u.email AS email
            FROM system_assignments sa
            JOIN users u ON u.id = sa.user_id
            WHERE sa.ai_system_id = :sid
              AND sa.role = 'authorized_representative'
            LIMIT 1
            """
            ),
            {"sid": system_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


# ---------------------------
# CRUD
# ---------------------------
@router.get("/ai-systems", response_model=List[AISystemOut])
def list_ai_systems(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    company_id: Optional[int] = Query(
        None, description="Optional filter by company_id"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """
    List AI systems.
      - SuperAdmin: all systems (optionally filtered by company_id)
      - Staff/Client Admin: systems in visible companies (own; staff admins also assigned)
      - Contributor: only explicitly assigned systems
    """
    if is_super(current_user):
        rows = (
            crud_get_systems_by_company_ids(db, [company_id], skip=skip, limit=limit)
            if company_id is not None
            else crud_get_all_systems(db, skip=skip, limit=limit)
        )
        return [_to_out(r) for r in rows]

    if is_contributor(current_user):
        assigned_ids = get_assigned_system_ids(db, current_user.id)
        if not assigned_ids:
            return []
        rows = (
            db.query(AISystem)
            .filter(AISystem.id.in_(assigned_ids))
            .order_by(AISystem.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return [_to_out(r) for r in rows]

    visible_company_ids = set()
    if current_user.company_id:
        visible_company_ids.add(current_user.company_id)
    if is_admin(current_user):
        visible_company_ids.update(get_assigned_company_ids(db, current_user.id))

    if company_id is not None:
        if company_id not in visible_company_ids:
            return []
        visible_company_ids = {company_id}

    if not visible_company_ids:
        return []

    rows = crud_get_systems_by_company_ids(
        db, list(visible_company_ids), skip=skip, limit=limit
    )
    return [_to_out(r) for r in rows]


@router.post(
    "/ai-systems", response_model=AISystemOut, status_code=status.HTTP_201_CREATED
)
def create_ai_system(
    payload: AISystemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    """
    Create AI system.
      - SuperAdmin: any company
      - Client Admin: own company
      - Staff Admin: assigned companies
      - Contributor: not allowed
    """
    if is_contributor(current_user):
        raise HTTPException(
            status_code=403, detail="Contributors cannot create AI systems"
        )

    if not can_write_company(db, current_user, payload.company_id):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    try:
        obj = crud_create_system(db, payload)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_CREATED",
            entity_type="ai_system",
            entity_id=obj.id,
            meta={
                "name": getattr(obj, "name", None),
                "risk_tier": getattr(obj, "risk_tier", None),
                "lifecycle_stage": getattr(obj, "lifecycle_stage", None),
            },
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.get("/ai-systems/{system_id}", response_model=AISystemOutExtended)
def get_ai_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AISystemOutExtended:
    """
    Return AISystem plus computed badges and current AR:
      - compliance_status_computed (from tasks)
      - effective_risk (derived from risk_tier + computed compliance)
      - authorized_representative (user_id, email) + legacy authorized_representative_user_id
    """
    system = ensure_system_access_read(db, current_user, system_id)

    base = AISystemOut.model_validate(system).model_dump()

    # Compliance badges
    agg = _fetch_system_compliance(db, system_id)
    effective_risk = compute_effective_risk(
        base.get("risk_tier"), agg["compliance_status"]
    )
    base["compliance_status_computed"] = agg["compliance_status"]
    base["effective_risk"] = effective_risk

    # Current AR (computed)
    ar = _fetch_current_ar(db, system_id)
    if ar:
        base["authorized_representative_user_id"] = int(ar["user_id"])
        base["authorized_representative"] = {
            "user_id": int(ar["user_id"]),
            "email": ar.get("email"),
        }
    else:
        base["authorized_representative_user_id"] = None
        base["authorized_representative"] = None

    return AISystemOutExtended(**base)


@router.put("/ai-systems/{system_id}", response_model=AISystemOut)
def update_ai_system(
    system_id: int,
    payload: AISystemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    """
    Update AI system.
      - Full edit: SuperAdmin, Client Admin (own), Staff Admin (assigned)
      - Limited edit: Contributor (only notes, status, lifecycle_stage)
    """
    system = ensure_system_write_limited(db, current_user, system_id)
    data = payload.model_dump(exclude_none=True)

    has_full = True
    try:
        ensure_system_write_full(db, current_user, system_id)
    except HTTPException:
        has_full = False

    if not has_full:
        illegal = set(data.keys()) - CONTRIBUTOR_ALLOWED_FIELDS
        if illegal:
            allowed = ", ".join(sorted(CONTRIBUTOR_ALLOWED_FIELDS))
            raise HTTPException(
                status_code=403,
                detail=f"Contributors can only update: {allowed}",
            )

    obj = crud_update_system(db, system, payload)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=obj.company_id,
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_UPDATED",
            entity_type="ai_system",
            entity_id=obj.id,
            meta={"changes": data},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return _to_out(obj)


@router.delete("/ai-systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_system(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
) -> Response:
    """
    Delete AI system.
      - SuperAdmin: allowed
      - Client Admin: allowed (own company)
      - Staff Admin: allowed (assigned companies)
      - Contributor: not allowed
    """
    system = ensure_system_write_full(db, current_user, system_id)

    meta_snapshot = {
        "name": getattr(system, "name", None),
        "risk_tier": getattr(system, "risk_tier", None),
        "lifecycle_stage": getattr(system, "lifecycle_stage", None),
        "company_id": getattr(system, "company_id", None),
    }

    crud_delete_system(db, system)

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=meta_snapshot["company_id"],
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_DELETED",
            entity_type="ai_system",
            entity_id=system_id,
            meta=meta_snapshot,
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------
# Risk Assessment – quick classify
# ---------------------------
@router.get(
    "/ai-systems/{system_id}/assessment-sample", response_model=RiskAssessmentAnswer
)
def get_assessment_sample(
    system_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_system_access_read(db, current_user, system_id)
    return RiskAssessmentAnswer.model_validate({})


@router.post("/ai-systems/{system_id}/assessment", response_model=RiskAssessmentResult)
def assess_ai_system(
    system_id: int,
    payload: RiskAssessmentAnswer,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,
):
    ensure_system_access_read(db, current_user, system_id)

    answers_dict: Dict[str, Any] = payload.model_dump(exclude_none=True)
    result_dict = classify_ai_system(answers_dict)

    obligations = result_dict.get("obligations", {})
    if isinstance(obligations, list):
        obligations = {"core": obligations, "situational": []}

    out = RiskAssessmentResult(
        system_id=system_id,
        risk_tier=result_dict.get("risk_tier", "minimal_risk"),
        obligations=obligations,
        rationale=result_dict.get("rationale", []),
        version="1.1.0",
    )

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=getattr(current_user, "company_id", None),
            user_id=getattr(current_user, "id", None),
            action="SYSTEM_ASSESSED",
            entity_type="ai_system",
            entity_id=system_id,
            meta={"summary_risk_tier": out.risk_tier},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    return out


# ---------------------------
# Company systems with KPI (badges)
# ---------------------------
@router.get("/company/{company_id}/systems")
def list_company_systems(
    company_id: int,
    member_user_id: Optional[int] = Query(
        None,
        description="Return only AI systems where this user is a member (ai_system_members)",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    ensure_company_access(current_user, company_id)

    base_sql = """
        SELECT
            s.id,
            s.name,
            s.company_id,
            s.risk_tier,
            s.lifecycle_stage,
            v.compliance_pct,
            v.overdue_cnt
        FROM ai_systems s
        LEFT JOIN vw_system_compliance v ON v.ai_system_id = s.id
        WHERE s.company_id = :cid
    """
    params = {"cid": company_id}

    if member_user_id is not None:
        base_sql += """
            AND EXISTS (
                SELECT 1 FROM ai_system_members m
                WHERE m.ai_system_id = s.id AND m.user_id = :muid
            )
        """
        params["muid"] = member_user_id

    base_sql += " ORDER BY COALESCE(v.compliance_pct, 100.0) ASC, s.name ASC"

    rows = db.execute(text(base_sql), params).mappings().all()
    items: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        pct = float(d.get("compliance_pct") or 0.0)
        overdue = int(d.get("overdue_cnt") or 0)
        cs = compliance_status_from_pct(pct, overdue)
        er = compute_effective_risk(d.get("risk_tier"), cs)
        d["compliance_status"] = cs
        d["effective_risk"] = er
        items.append(d)
    return items


# ---------------------------
# List by member (RBAC-safe)
# ---------------------------
@router.get("/ai-systems/by-member/{member_user_id}", response_model=List[AISystemOut])
def list_ai_systems_by_member(
    member_user_id: int,
    company_id: Optional[int] = Query(
        None, description="Limit to company_id (if null, use current user's company)"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return AI systems where 'member_user_id' is a member (ai_system_members).
      - SuperAdmin: any company (optionally filtered by company_id)
      - Others: only within their own company (or provided company_id that matches theirs)
    """
    if not is_super(current_user):
        scoped_cid = (
            company_id
            if company_id is not None
            else getattr(current_user, "company_id", None)
        )
        if scoped_cid is None:
            return []
        ensure_company_access(current_user, scoped_cid)
        cid_clause = " AND s.company_id = :cid"
        params: Dict[str, Any] = {"muid": member_user_id, "cid": scoped_cid}
    else:
        cid_clause = ""
        params = {"muid": member_user_id}

    sql = f"""
        SELECT s.*
        FROM ai_systems s
        WHERE EXISTS (
            SELECT 1 FROM ai_system_members m
            WHERE m.ai_system_id = s.id AND m.user_id = :muid
        ){cid_clause}
        ORDER BY s.id DESC
        LIMIT 500
    """
    rows = db.execute(text(sql), params).mappings().all()
    return [_to_out(AISystem(**dict(r))) for r in rows]


# ---------------------------
# Authorized Representative (AR) assignment + optional handover
# ---------------------------
@router.post(
    "/ai-systems/{system_id}/assign-ar", status_code=status.HTTP_204_NO_CONTENT
)
def assign_authorized_representative(
    system_id: int,
    payload: AssignARRequest,
    request: Request,
    handover: bool = Query(False, description="If true, record AR_HANDOVER in audit"),
    reassign_open_tasks: bool = Query(
        False, description="If true, move open tasks to the new AR"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Assign Authorized Representative (AR) to a system.
    Permissions: full write on the system (Company Admin / SuperAdmin / assigned Staff Admin with full).

    If `handover=true` and an AR existed before, we also record AR_HANDOVER in audit.
    If `reassign_open_tasks=true`, open compliance tasks owned by the old AR are reassigned to the new AR.
    """
    system = get_system_or_404(db, system_id)
    ensure_system_write_full(db, current_user, system_id)

    # remember old AR (if any) before we unset
    old_ar = (
        db.query(SystemAssignment)
        .filter(
            SystemAssignment.ai_system_id == system_id,
            SystemAssignment.role == "authorized_representative",
        )
        .first()
    )

    ar_user = validate_user_in_company(db, payload.user_id, system.company_id)

    # enforce single AR per system
    removed = unset_ar_assignment(db, system_id)
    ar_assignment = SystemAssignment(
        ai_system_id=system_id,
        user_id=ar_user.id,
        role="authorized_representative",
    )
    db.add(ar_assignment)
    db.commit()

    # optional: move open tasks from old AR -> new AR
    moved_tasks = 0
    if handover and reassign_open_tasks and old_ar and ComplianceTask is not None:
        moved_tasks = (
            db.query(ComplianceTask)
            .filter(
                ComplianceTask.ai_system_id == system_id,
                ComplianceTask.owner_user_id == old_ar.user_id,
                ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
            )
            .update(
                {ComplianceTask.owner_user_id: ar_user.id}, synchronize_session=False
            )
        )
        db.commit()

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="AR_ASSIGNED",
            entity_type="ai_system",
            entity_id=system_id,
            meta={"ar_user_id": ar_user.id, "replaced_cnt": removed},
            ip=ip_from_request(request),
        )
        if handover and old_ar:
            audit_log(
                db,
                company_id=system.company_id,
                user_id=current_user.id,
                action="AR_HANDOVER",
                entity_type="ai_system",
                entity_id=system_id,
                meta={
                    "from_user_id": old_ar.user_id,
                    "to_user_id": ar_user.id,
                    "tasks_reassigned": int(moved_tasks),
                },
                ip=ip_from_request(request),
            )
        db.commit()
    except Exception:
        db.rollback()

    # NOTIFICATION (best-effort)
    try:
        if callable(produce_ar_assigned):
            produce_ar_assigned(  # type: ignore
                db,
                company_id=system.company_id,
                ai_system_id=system_id,
                ar_user_id=ar_user.id,
                set_by_user_id=current_user.id,
            )
    except Exception:
        pass

    return None  # 204


@router.delete(
    "/ai-systems/{system_id}/assign-ar", status_code=status.HTTP_204_NO_CONTENT
)
def unassign_authorized_representative(
    system_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Unassign Authorized Representative (AR) from a system (idempotent if none assigned).
    Permissions: full write on the system.
    """
    system = get_system_or_404(db, system_id)
    ensure_system_write_full(db, current_user, system_id)

    removed = unset_ar_assignment(db, system_id)
    db.commit()

    # AUDIT (best-effort)
    try:
        audit_log(
            db,
            company_id=system.company_id,
            user_id=current_user.id,
            action="AR_UNASSIGNED",
            entity_type="ai_system",
            entity_id=system_id,
            meta={"removed_cnt": removed},
            ip=ip_from_request(request),
        )
        db.commit()
    except Exception:
        db.rollback()

    # NOTIFICATION (best-effort)
    try:
        if callable(produce_ar_unassigned):
            produce_ar_unassigned(  # type: ignore
                db,
                company_id=system.company_id,
                ai_system_id=system_id,
                unset_by_user_id=current_user.id,
            )
    except Exception:
        pass

    return None  # 204


# ---------------------------
# BULK AR TRANSFER
# ---------------------------
@router.post("/ai-systems/assign-ar/bulk", response_model=BulkTransferResult)
def bulk_assign_authorized_representative(
    payload: BulkTransferRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BulkTransferResult:
    """
    Bulk reassignment of Authorized Representative (AR) across multiple systems.

    AuthZ:
      - Must have company write for the target company (when using company_id/filters), OR
      - Must have full write on each explicit system_id.
    Target user must belong to each system's company.

    Behavior:
      - If dry_run=True, nothing is changed; you get a preview (which systems would be updated,
        and how many tasks would be reassigned if 'reassign_open_tasks=True').
      - If handover=True and a previous AR exists, AR_HANDOVER is recorded in audit.
      - If reassign_open_tasks=True, open compliance tasks owned by the old AR are reassigned to the new AR.
    """
    # Build selection SQL
    clauses = []
    params: Dict[str, Any] = {}

    if payload.system_ids:
        # use expanding bind param for SQL IN
        clauses.append("s.id IN :ids")
        params["ids"] = list({int(x) for x in payload.system_ids})

    if payload.company_id:
        clauses.append("s.company_id = :cid")
        params["cid"] = int(payload.company_id)

    if payload.filter:
        f = payload.filter
        if f.risk_tier:
            clauses.append("LOWER(COALESCE(s.risk_tier,'')) = :rt")
            params["rt"] = f.risk_tier.strip().lower()
        if f.lifecycle_stage:
            clauses.append("LOWER(COALESCE(s.lifecycle_stage,'')) = :ls")
            params["ls"] = f.lifecycle_stage.strip().lower()
        if f.status:
            clauses.append("LOWER(COALESCE(s.status,'')) = :st")
            params["st"] = f.status.strip().lower()
        if f.name_ilike:
            clauses.append("LOWER(s.name) LIKE :namelike")
            params["namelike"] = f"%{f.name_ilike.strip().lower()}%"
        if f.from_user_id:
            clauses.append(
                """EXISTS (
                    SELECT 1 FROM system_assignments sa
                    WHERE sa.ai_system_id = s.id
                      AND sa.role = 'authorized_representative'
                      AND sa.user_id = :from_uid
                )"""
            )
            params["from_uid"] = int(f.from_user_id)

    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
        SELECT s.id, s.company_id, s.name
        FROM ai_systems s
        {where_sql}
        ORDER BY s.id ASC
        LIMIT 10000
    """
    stmt = text(sql)
    if "ids" in params:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))

    rows = db.execute(stmt, params).mappings().all()
    systems: List[Dict[str, Any]] = [dict(r) for r in rows]

    total_scanned = len(systems)
    updated_ids: List[int] = []
    skipped: List[SkippedItem] = []
    tasks_reassigned = 0

    # If using company scope, quick upfront permission check
    if payload.company_id is not None:
        if not can_write_company(db, current_user, int(payload.company_id)):
            raise HTTPException(
                status_code=403, detail="Insufficient privileges for the target company"
            )

    # Process each system
    for srow in systems:
        sid = int(srow["id"])
        scid = int(srow["company_id"])

        # If not using company_id, ensure full write per system
        if payload.company_id is None:
            try:
                ensure_system_write_full(db, current_user, sid)
            except HTTPException:
                skipped.append(
                    SkippedItem(system_id=sid, reason="insufficient privileges")
                )
                continue

        # Target user must be in the same company
        try:
            target_user = validate_user_in_company(
                db, payload.to_user_id, scid
            )  # noqa: F841 (used for validation)
        except HTTPException as e:
            skipped.append(
                SkippedItem(system_id=sid, reason=e.detail or "invalid target user")
            )
            continue

        current_ar = _fetch_current_ar(db, sid)
        if current_ar and int(current_ar["user_id"]) == int(payload.to_user_id):
            skipped.append(
                SkippedItem(system_id=sid, reason="already assigned to target user")
            )
            continue

        # DRY-RUN path: only estimate effects
        if payload.dry_run:
            updated_ids.append(sid)
            if (
                payload.reassign_open_tasks
                and current_ar
                and ComplianceTask is not None
            ):
                cnt_row = (
                    db.execute(
                        text(
                            """
                        SELECT COUNT(1) AS cnt
                        FROM compliance_tasks
                        WHERE ai_system_id = :sid
                          AND owner_user_id = :old_uid
                          AND (status IS NULL OR LOWER(status) NOT IN ('done','cancelled'))
                        """
                        ),
                        {"sid": sid, "old_uid": int(current_ar["user_id"])},
                    )
                    .mappings()
                    .first()
                )
                tasks_reassigned += int(cnt_row["cnt"] or 0)
            continue

        # MUTATING path
        removed = unset_ar_assignment(db, sid)
        db.add(
            SystemAssignment(
                ai_system_id=sid,
                user_id=int(payload.to_user_id),
                role="authorized_representative",
            )
        )
        db.commit()
        moved_here = 0

        if payload.reassign_open_tasks and current_ar and ComplianceTask is not None:
            moved_here = (
                db.query(ComplianceTask)
                .filter(
                    ComplianceTask.ai_system_id == sid,
                    ComplianceTask.owner_user_id == int(current_ar["user_id"]),
                    ~func.lower(ComplianceTask.status).in_(("done", "cancelled")),
                )
                .update(
                    {ComplianceTask.owner_user_id: int(payload.to_user_id)},
                    synchronize_session=False,
                )
            )
            db.commit()
            tasks_reassigned += int(moved_here)

        # AUDIT (best-effort)
        try:
            audit_log(
                db,
                company_id=scid,
                user_id=getattr(current_user, "id", None),
                action="AR_ASSIGNED",
                entity_type="ai_system",
                entity_id=sid,
                meta={"ar_user_id": int(payload.to_user_id), "replaced_cnt": removed},
                ip=ip_from_request(request),
            )
            if payload.handover and current_ar:
                audit_log(
                    db,
                    company_id=scid,
                    user_id=getattr(current_user, "id", None),
                    action="AR_HANDOVER",
                    entity_type="ai_system",
                    entity_id=sid,
                    meta={
                        "from_user_id": int(current_ar["user_id"]),
                        "to_user_id": int(payload.to_user_id),
                        "tasks_reassigned": int(moved_here),
                    },
                    ip=ip_from_request(request),
                )
            db.commit()
        except Exception:
            db.rollback()

        # NOTIFICATION (best-effort)
        try:
            if callable(produce_ar_assigned):
                produce_ar_assigned(  # type: ignore
                    db,
                    company_id=scid,
                    ai_system_id=sid,
                    ar_user_id=int(payload.to_user_id),
                    set_by_user_id=getattr(current_user, "id", None),
                )
        except Exception:
            pass

        updated_ids.append(sid)

    return BulkTransferResult(
        total_scanned=total_scanned,
        updated=len(updated_ids),
        tasks_reassigned=int(tasks_reassigned),
        updated_system_ids=updated_ids,
        skipped=skipped,
        dry_run=bool(payload.dry_run),
        to_user_id=int(payload.to_user_id),
    )
