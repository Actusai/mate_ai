# app/api/v1/audit_logs.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import text
import json

from app.core.auth import get_db, get_current_user
from app.models.user import User

router = APIRouter(prefix="/audit", tags=["audit"])

# --- helpers ---


def _ensure_superadmin(user: User) -> None:
    if not bool(getattr(user, "is_super_admin", False)):
        raise HTTPException(status_code=403, detail="Super Admin only")


def _safe_int(x: Optional[int]) -> Optional[int]:
    try:
        return int(x) if x is not None else None
    except Exception:
        return None


def _safe_json_loads(s: Optional[str]) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        # ako nije validan JSON (npr. legacy zapisi), vrati raw string
        return s


# --- endpoints ---


@router.get("/logs")
def list_audit_logs(
    # filteri
    company_id: Optional[int] = Query(None, description="Filter po company_id"),
    user_id: Optional[int] = Query(
        None, description="Filter po user_id koji je izvršio akciju"
    ),
    action: Optional[str] = Query(
        None, description="Exact match po action (npr. TASK_UPDATED)"
    ),
    entity_type: Optional[str] = Query(
        None, description="Exact match po entity_type (npr. compliance_task)"
    ),
    entity_id: Optional[int] = Query(None, description="Exact match po entity_id"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    q: Optional[str] = Query(
        None, description="Full-text like po action/entity_type/meta (SQLite LIKE)"
    ),
    # paginacija/sort
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    order_by: Optional[str] = Query(
        "created_at", description="Sort kolona: id|created_at"
    ),
    order_dir: str = Query("desc", regex="^(?i)(asc|desc)$"),
    # deps
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    response: Response = None,
) -> Dict[str, Any]:
    """
    SuperAdmin pregled audita s filterima, paginacijom i sortiranjem.
    Vraća items + pagination meta. Header X-Total-Count sadrži ukupan broj zapisa.
    """
    _ensure_superadmin(current_user)

    allowed_sort = {"id", "created_at"}
    if order_by not in allowed_sort:
        order_by = "created_at"
    order_sql = "ASC" if order_dir.lower() == "asc" else "DESC"

    filters: List[str] = []
    params: Dict[str, Any] = {}

    if company_id is not None:
        filters.append("company_id = :cid")
        params["cid"] = _safe_int(company_id)
    if user_id is not None:
        filters.append("user_id = :uid")
        params["uid"] = _safe_int(user_id)
    if action:
        filters.append("action = :act")
        params["act"] = action
    if entity_type:
        filters.append("entity_type = :etype")
        params["etype"] = entity_type
    if entity_id is not None:
        filters.append("entity_id = :eid")
        params["eid"] = _safe_int(entity_id)
    if date_from:
        # uključivo od početka dana
        filters.append("created_at >= datetime(:dfrom)")
        params["dfrom"] = f"{date_from} 00:00:00"
    if date_to:
        # uključivo do kraja dana
        filters.append("created_at <= datetime(:dto, '+1 day', '-1 second')")
        params["dto"] = date_to
    if q:
        # jednostavan LIKE na nekoliko polja
        filters.append(
            "(lower(action) LIKE :qq OR lower(entity_type) LIKE :qq OR lower(meta) LIKE :qq)"
        )
        params["qq"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    # count
    count_sql = f"SELECT COUNT(*) AS cnt FROM audit_logs {where_sql}"
    total = db.execute(text(count_sql), params).scalar() or 0

    # list
    list_sql = f"""
        SELECT
            id, company_id, user_id, action, entity_type, entity_id,
            meta, ip_address, created_at
        FROM audit_logs
        {where_sql}
        ORDER BY {order_by} {order_sql}
        LIMIT :lim OFFSET :skp
    """
    params_list = dict(params)
    params_list.update({"lim": limit, "skp": skip})

    rows = db.execute(text(list_sql), params_list).mappings().all()

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "company_id": r["company_id"],
                "user_id": r["user_id"],
                "action": r["action"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "ip_address": r.get("ip_address"),
                "created_at": r["created_at"],
                # meta: probaj parse u dict, ako ne uspije – vrati raw string
                "meta": _safe_json_loads(r.get("meta")),
            }
        )

    # stavimo total u header radi lakše paginacije na frontendu
    if response is not None:
        response.headers["X-Total-Count"] = str(total)

    return {
        "items": items,
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": total,
        },
        "filters": {
            "company_id": company_id,
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "date_from": date_from,
            "date_to": date_to,
            "q": q,
        },
        "order": {
            "order_by": order_by,
            "order_dir": order_dir.lower(),
        },
    }
