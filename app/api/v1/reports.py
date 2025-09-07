# app/api/v1/reports.py
from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
import io, csv, json
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.services.reporting import (
    compute_company_kpis,
    systems_table,
    overdue_by_owner,
    upcoming_deadlines,
    team_overview,
    reference_breakdown,
    compute_superadmin_overview,
    company_alerts,
    # helpers to enrich system_compliance export
    compliance_status_from_pct,
    compute_effective_risk,
)
from app.services.audit import audit_export, ip_from_request
from app.services.snapshots import run_snapshots
from app.core.rbac import (
    ensure_export_access,
    ensure_member_filter_access,
)
# AR scenario: staff admin may access assigned companies
from app.core.scoping import is_assigned_admin, is_super

router = APIRouter(prefix="/reports", tags=["reports"])


def _ensure_company_access(current_user: User, company_id: int, db: Session | None = None) -> None:
    """
    Allow:
      - super admin
      - user from the same company
      - staff/client admin assigned to that client company
    """
    if is_super(current_user):
        return
    if getattr(current_user, "company_id", None) == company_id:
        return
    if db and is_assigned_admin(db, current_user, company_id):
        return
    raise HTTPException(status_code=403, detail="Insufficient privileges")


def _ensure_superadmin(current_user: User) -> None:
    if not bool(getattr(current_user, "is_super_admin", False)):
        raise HTTPException(status_code=403, detail="Super Admin only")


@router.get("/company/{company_id}/dashboard")
def company_dashboard(
    company_id: int,
    window_days: int = Query(30, ge=1, le=365),
    due_in_days: int = Query(14, ge=1, le=90),
    alerts_limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    _ensure_company_access(current_user, company_id, db)

    kpis = compute_company_kpis(db, company_id, window_days=window_days)
    systems = systems_table(db, company_id)
    overdue = overdue_by_owner(db, company_id, limit=5)
    deadlines = upcoming_deadlines(db, company_id, in_days=due_in_days)
    team = team_overview(db, company_id)
    refs = reference_breakdown(db, company_id)
    alerts = company_alerts(db, company_id, limit=alerts_limit)

    return {
        "company_id": company_id,
        "kpi": kpis,
        "systems": systems,
        "overdue_by_owner": overdue,
        "upcoming_deadlines": deadlines,
        "team": team,
        "reference_breakdown": refs,
        "alerts": alerts,
    }


@router.get("/superadmin/overview")
def superadmin_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    _ensure_superadmin(current_user)
    return compute_superadmin_overview(db)


# -----------------------------
# EXPORT (CSV / JSON / XLSX)  →  /api/v1/reports/export
# -----------------------------
@router.get("/export")
def export_data(
    request: Request,   # keep Request without default to avoid FastAPI/Pydantic errors
    type: str = Query(
        ...,
        description="Data type: company_compliance, system_compliance, compliance_tasks, users, ai_systems, audit_logs, reference_breakdown, task_status, incidents",
    ),
    # common filters
    ai_system_id: Optional[int] = Query(None, description="Filter by AI system ID"),
    member_user_id: Optional[int] = Query(None, description="Filter by member assigned to AI systems"),
    company_id: Optional[int] = Query(None, description="Export data for specific company (AR: client)"),
    # incidents-only filters (applied when type == 'incidents')
    incident_status: Optional[str] = Query(None, description="Incident status filter (new|investigating|reported|closed)"),
    incident_severity: Optional[str] = Query(None, description="Incident severity filter (low|medium|high|critical)"),
    incident_type: Optional[str] = Query(None, description="Incident type filter"),
    incident_date_from: Optional[str] = Query(None, description="Incident occurred_at >= this ISO date/datetime"),
    incident_date_to: Optional[str] = Query(None, description="Incident occurred_at <= this ISO date/datetime"),
    # format & output
    format: str = Query("csv", regex="^(?i)(csv|json|xlsx)$"),
    columns: Optional[str] = Query(None, description="Whitelist of columns, e.g.: id,company_id,ai_system_id,status"),
    redact: Optional[str] = Query(None, description="Columns to redact ('***'), e.g.: notes,evidence_url"),
    # CSV UX/safety
    limit: int = Query(20000, ge=1, le=200000, description="Max rows in export"),
    order_by: Optional[str] = Query(None, description="Sort column (allowlist per type)"),
    order_dir: str = Query("desc", regex="^(?i)(asc|desc)$"),
    sep: str = Query("semicolon", regex="^(comma|semicolon|tab)$", description="CSV delimiter"),
    bom: bool = Query(False, description="Add UTF-8 BOM (Excel friendly)"),
    safe_csv: bool = Query(True, description="Protect against CSV formula injection"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export_map = {
        "company_compliance": {"table": "vw_company_compliance", "restricted": True},
        "system_compliance": {"table": "vw_system_compliance", "restricted": False},
        "task_status": {"table": "vw_task_status_counts", "restricted": False},
        "reference_breakdown": {"table": "vw_reference_breakdown", "restricted": False},
        "compliance_tasks": {"table": "compliance_tasks", "restricted": False},
        "audit_logs": {"table": "audit_logs", "restricted": False},
        "users": {"table": "users", "restricted": False},
        "ai_systems": {"table": "ai_systems", "restricted": False},
        # NEW
        "incidents": {"table": "incidents", "restricted": False},
    }

    # PII-safe column allowlist (also used to validate requested columns)
    column_allowlist: dict[str, set[str]] = {
        "company_compliance": {"company_id", "systems_cnt", "avg_compliance_pct", "overdue_cnt"},
        # system_compliance: now exposes risk_tier and derived columns
        "system_compliance": {"ai_system_id", "company_id", "compliance_pct", "overdue_cnt", "risk_tier", "compliance_status", "effective_risk"},
        "task_status": {"company_id", "ai_system_id", "open_cnt", "in_progress_cnt", "blocked_cnt", "postponed_cnt", "done_cnt"},
        "reference_breakdown": {"company_id", "reference", "total", "done_cnt", "overdue_cnt"},
        "compliance_tasks": {
            "id", "company_id", "ai_system_id", "title", "status", "severity", "mandatory",
            "owner_user_id", "due_date", "completed_at", "created_at", "updated_at",
            "evidence_url", "notes", "priority", "reference", "reminder_days_before"
        },
        "audit_logs": {"id", "company_id", "user_id", "action", "entity_type", "entity_id", "created_at", "ip_address"},
        "users": {"id", "company_id", "email", "role", "invite_status", "is_active", "last_login_at", "created_at"},
        "ai_systems": {"id", "company_id", "name", "risk_tier", "lifecycle_stage", "status", "owner_user_id", "last_activity_at"},
        # NEW: incidents
        "incidents": {
            "id", "company_id", "ai_system_id", "reported_by",
            "occurred_at", "severity", "type", "summary", "details_json",
            "status", "created_at", "updated_at",
        },
    }

    # ORDER allowlist: mostly mirrors columns; excludes derived fields not in SQL
    order_allowlist: dict[str, set[str]] = {
        "company_compliance": {"company_id", "systems_cnt", "avg_compliance_pct", "overdue_cnt"},
        "system_compliance": {"ai_system_id", "company_id", "compliance_pct", "overdue_cnt", "risk_tier"},  # no sort by derived fields
        "task_status": {"company_id", "ai_system_id", "open_cnt", "in_progress_cnt", "blocked_cnt", "postponed_cnt", "done_cnt"},
        "reference_breakdown": {"company_id", "reference", "total", "done_cnt", "overdue_cnt"},
        "compliance_tasks": {
            "id", "company_id", "ai_system_id", "title", "status", "severity", "mandatory",
            "owner_user_id", "due_date", "completed_at", "created_at", "updated_at",
            "reference", "reminder_days_before"
        },
        "audit_logs": {"id", "company_id", "user_id", "action", "entity_type", "entity_id", "created_at", "ip_address"},
        "users": {"id", "company_id", "email", "role", "invite_status", "is_active", "last_login_at", "created_at"},
        "ai_systems": {"id", "company_id", "name", "risk_tier", "lifecycle_stage", "status", "owner_user_id", "last_activity_at"},
        # NEW
        "incidents": {"id", "company_id", "ai_system_id", "reported_by", "occurred_at", "severity", "type", "status", "created_at", "updated_at"},
    }

    entry = export_map.get(type)
    if not entry:
        raise HTTPException(status_code=400, detail="Unknown export type")

    table = entry["table"]
    is_restricted = entry["restricted"]

    # RBAC for 'company_compliance'
    if is_restricted and not is_super(current_user):
        if type == "company_compliance" and company_id is not None:
            same_company = (company_id == getattr(current_user, "company_id", None))
            if not (same_company or is_assigned_admin(db, current_user, company_id)):
                raise HTTPException(status_code=403, detail="Forbidden (not assigned to this company)")
        else:
            raise HTTPException(status_code=403, detail="Insufficient privileges for this export")

    # Guards for filters
    ensure_member_filter_access(current_user, member_user_id)
    ensure_export_access(db, current_user, ai_system_id)

    # Build base query (special-case system_compliance to bring risk_tier)
    if type == "system_compliance":
        base_query = """
            SELECT
                v.ai_system_id AS ai_system_id,
                v.company_id   AS company_id,
                v.compliance_pct AS compliance_pct,
                v.overdue_cnt    AS overdue_cnt,
                s.risk_tier      AS risk_tier
            FROM vw_system_compliance v
            LEFT JOIN ai_systems s ON s.id = v.ai_system_id
        """
    else:
        base_query = f"SELECT * FROM {table}"

    params: Dict[str, Any] = {}
    filters: List[str] = []

    # Company scoping
    if not is_super(current_user):
        if company_id is not None:
            same_company = (company_id == getattr(current_user, "company_id", None))
            if not (same_company or is_assigned_admin(db, current_user, company_id)):
                raise HTTPException(status_code=403, detail="Forbidden (not assigned to this company)")
            filters.append("company_id = :cid")
            params["cid"] = company_id
        else:
            if table in {
                "vw_system_compliance",
                "vw_task_status_counts",
                "vw_reference_breakdown",
                "compliance_tasks",
                "audit_logs",
                "users",
                "ai_systems",
                # NEW
                "incidents",
            }:
                filters.append("company_id = :cid")
                params["cid"] = current_user.company_id

    # SuperAdmin may optionally scope company_compliance by company_id
    if is_super(current_user) and type == "company_compliance" and company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = company_id

    # ai_system_id filter
    if ai_system_id is not None:
        if table == "ai_systems":
            filters.append("id = :aid")
            params["aid"] = ai_system_id
        elif table in {"vw_task_status_counts", "vw_reference_breakdown", "compliance_tasks", "vw_system_compliance", "incidents"}:
            filters.append("ai_system_id = :aid")
            params["aid"] = ai_system_id

    # member_user_id filter
    if member_user_id is not None:
        if table == "ai_systems":
            filters.append("id IN (SELECT ai_system_id FROM ai_system_members WHERE user_id = :muid)")
            params["muid"] = member_user_id
        elif table in {"compliance_tasks", "vw_task_status_counts", "vw_reference_breakdown"}:
            filters.append("ai_system_id IN (SELECT ai_system_id FROM ai_system_members WHERE user_id = :muid)")
            params["muid"] = member_user_id

    # Incidents-only filters
    if type == "incidents":
        if incident_status:
            filters.append("status = :istatus")
            params["istatus"] = incident_status
        if incident_severity:
            filters.append("severity = :iseverity")
            params["iseverity"] = incident_severity
        if incident_type:
            filters.append("type = :itype")
            params["itype"] = incident_type
        # Normalize YYYY-MM-DD to full-day bounds
        if incident_date_from:
            params["idfrom"] = incident_date_from + (" 00:00:00" if len(incident_date_from) == 10 else "")
            filters.append("occurred_at >= :idfrom")
        if incident_date_to:
            params["idto"] = incident_date_to + (" 23:59:59" if len(incident_date_to) == 10 else "")
            filters.append("occurred_at <= :idto")

    # WHERE
    query = base_query
    if filters:
        query += " WHERE " + " AND ".join(filters)

    # ORDER BY (allowlist)
    if order_by:
        allowed = order_allowlist.get(type, set())
        if order_by not in allowed:
            raise HTTPException(status_code=400, detail=f"Invalid sort column for '{type}'")
        query += f" ORDER BY {order_by} {order_dir.upper()}"

    # LIMIT +1 → truncation signal
    query += " LIMIT :lim_plus_one"
    params["lim_plus_one"] = int(limit) + 1

    # Fetch
    rows = db.execute(text(query), params).mappings().all()
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    if not rows:
        raise HTTPException(status_code=404, detail="No data found for the given parameters")

    # Enrich system_compliance with derived fields (compliance_status, effective_risk)
    if type == "system_compliance":
        enriched: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            pct = float(d.get("compliance_pct") or 0.0)
            overdue = int(d.get("overdue_cnt") or 0)
            cs = compliance_status_from_pct(pct, overdue)
            er = compute_effective_risk(d.get("risk_tier"), cs)
            d["compliance_status"] = cs
            d["effective_risk"] = er
            enriched.append(d)
        rows = enriched

    # Columns whitelist & redaction
    result_fieldnames = list(rows[0].keys())
    allowed_cols = column_allowlist.get(type, set()) or set(result_fieldnames)

    requested_cols: Optional[List[str]] = None
    if columns:
        requested_cols = [c.strip() for c in columns.split(",") if c.strip()]
    if requested_cols:
        export_cols = [c for c in requested_cols if c in allowed_cols and c in result_fieldnames]
        if not export_cols:
            raise HTTPException(status_code=400, detail="No valid columns after applying allowlist.")
    else:
        export_cols = [c for c in result_fieldnames if c in allowed_cols] or result_fieldnames

    redact_set = set()
    if redact:
        redact_set = {c.strip() for c in redact.split(",") if c.strip()}

    # JSON serialization helpers
    from datetime import date as _date, datetime as _dt
    from decimal import Decimal as _Decimal

    def _jsonable(v: Any) -> Any:
        if isinstance(v, (_dt, _date)):
            return v.isoformat()
        if isinstance(v, _Decimal):
            return str(v)
        return v

    def project_and_redact(r: Dict[str, Any], *, json_safe: bool) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k in export_cols:
            v = r.get(k)
            if k in redact_set:
                out[k] = "***" if v is not None else None
            else:
                out[k] = _jsonable(v) if json_safe else v
        return out

    fmt = format.lower()
    json_safe = (fmt != "csv")
    projected_rows = [project_and_redact(dict(r), json_safe=json_safe) for r in rows]

    # JSON
    if fmt == "json":
        payload = {
            "items": projected_rows,
            "truncated": truncated,
            "count": len(projected_rows),
        }
        try:
            audit_export(
                db,
                company_id=(params.get("cid") or getattr(current_user, "company_id", 0) or 0),
                user_id=getattr(current_user, "id", None),
                export_type=f"{type}:json",
                table_or_view=table,
                row_count=len(projected_rows),
                ip=ip_from_request(request),
                extras={
                    "ai_system_id": ai_system_id,
                    "member_user_id": member_user_id,
                    "truncated": truncated,
                    "company_id": company_id,
                    "columns": export_cols,
                    "redact": list(redact_set),
                },
            )
            db.commit()
        except Exception:
            db.rollback()
        return JSONResponse(content=payload)

    # XLSX
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="XLSX export requires 'openpyxl' package. Install it to enable XLSX export."
            )
        wb = Workbook()
        ws = wb.active
        ws.title = type[:31] or "export"
        ws.append(export_cols)
        for r in projected_rows:
            ws.append([r.get(k) for k in export_cols])

        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        # audit
        try:
            audit_export(
                db,
                company_id=(params.get("cid") or getattr(current_user, "company_id", 0) or 0),
                user_id=getattr(current_user, "id", None),
                export_type=f"{type}:xlsx",
                table_or_view=table,
                row_count=len(projected_rows),
                ip=ip_from_request(request),
                extras={
                    "ai_system_id": ai_system_id,
                    "member_user_id": member_user_id,
                    "truncated": truncated,
                    "company_id": company_id,
                    "columns": export_cols,
                    "redact": list(redact_set),
                },
            )
            db.commit()
        except Exception:
            db.rollback()

        ts = _dt.utcnow().strftime("%Y%m%d-%H%M%S")
        parts = []
        if not is_super(current_user):
            cid_for_name = params.get("cid") or getattr(current_user, "company_id", None)
            if cid_for_name is not None:
                parts.append(f"cid{cid_for_name}")
        elif company_id is not None:
            parts.append(f"cid{company_id}")
        if ai_system_id:
            parts.append(f"aid{ai_system_id}")
        if member_user_id:
            parts.append(f"mid{member_user_id}")
        suffix = ("_" + "_".join(parts)) if parts else ""
        filename = f"{type}{suffix}_{ts}.xlsx"

        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-store",
            "X-Export-Truncated": "1" if truncated else "0",
        }
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    # CSV (default)
    delimiter = {"comma": ",", "semicolon": ";", "tab": "\t"}[sep]

    def _sanitize_cell(v: Any) -> Any:
        if not safe_csv:
            return v
        if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@"):
            return "'" + v
        return v

    output = io.StringIO()
    if bom:
        output.write("\ufeff")  # UTF-8 BOM for Excel
    writer = csv.DictWriter(output, fieldnames=export_cols, delimiter=delimiter)
    writer.writeheader()
    for r in projected_rows:
        writer.writerow({k: _sanitize_cell(r.get(k)) for k in export_cols})
    output.seek(0)

    # audit
    try:
        audit_export(
            db,
            company_id=(params.get("cid") or getattr(current_user, "company_id", 0) or 0),
            user_id=getattr(current_user, "id", None),
            export_type=f"{type}:csv",
            table_or_view=table,
            row_count=len(projected_rows),
            ip=ip_from_request(request),
            extras={
                "ai_system_id": ai_system_id,
                "member_user_id": member_user_id,
                "truncated": truncated,
                "company_id": company_id,
                "columns": export_cols,
                "redact": list(redact_set),
            },
        )
        db.commit()
    except Exception:
        db.rollback()

    ts = _dt.utcnow().strftime("%Y%m%d-%H%M%S")
    parts = []
    if not is_super(current_user):
        cid_for_name = params.get("cid") or getattr(current_user, "company_id", None)
        if cid_for_name is not None:
            parts.append(f"cid{cid_for_name}")
    elif company_id is not None:
        parts.append(f"cid{company_id}")
    if ai_system_id:
        parts.append(f"aid{ai_system_id}")
    if member_user_id:
        parts.append(f"mid{member_user_id}")
    suffix = ("_" + "_".join(parts)) if parts else ""
    filename = f"{type}{suffix}_{ts}.csv"

    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Cache-Control": "no-store",
        "X-Export-Truncated": "1" if truncated else "0",
    }
    return StreamingResponse(output, media_type="text/csv; charset=utf-8", headers=headers)


# =============================
# Admin endpoint to trigger snapshots (SuperAdmin only)
# =============================
@router.post("/admin/run-snapshots")
def admin_run_snapshots(
    day: Optional[str] = Query(None, description="YYYY-MM-DD; default today (UTC)"),
    company_id: Optional[int] = Query(None, description="If set, run only for this company"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    _ensure_superadmin(current_user)
    res = run_snapshots(db, snapshot_day=day, company_id=company_id)
    return {"ok": True, **res}