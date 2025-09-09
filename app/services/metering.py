# app/services/metering.py
from __future__ import annotations
import os
from datetime import date, datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, Request, Response, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.auth import get_db, get_current_user
from app.models.user import User
from app.services.audit import audit_log, ip_from_request

# =============================
# Global kill-switch (default OFF)
# =============================
METERING_ENABLED = os.getenv("METERING_ENABLED", "false").lower() == "true"

# -----------------------------
# Helpers
# -----------------------------
def _month_bounds(d: date) -> Tuple[str, str]:
    start = d.replace(day=1)
    if start.month == 12:
        next_start = start.replace(year=start.year + 1, month=1)
    else:
        next_start = start.replace(month=start.month + 1)
    return start.strftime("%Y-%m-%d"), next_start.strftime("%Y-%m-%d")

def _month_reset_iso() -> str:
    """ISO vrijeme početka idućeg mjeseca (UTC)."""
    today = date.today()
    _, next_start = _month_bounds(today)
    return f"{next_start}T00:00:00Z"

def _find_company_package(db: Session, company_id: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Vrati (package_id, request_quota_month) ili (None, None).
    Siguran fallback: ako tablice/kolone ne postoje -> (None, None).
    """
    try:
        row = db.execute(
            text(
                """
                SELECT p.id AS package_id, p.request_quota_month AS quota
                FROM company_packages cp
                JOIN packages p ON p.id = cp.package_id
                WHERE cp.company_id = :cid
                ORDER BY cp.id DESC
                LIMIT 1
                """
            ),
            {"cid": company_id},
        ).mappings().first()
        if not row:
            return None, None
        # Ako kolona ne postoji / None:
        return row.get("package_id"), row.get("quota")
    except Exception:
        return None, None

def _current_month_usage(db: Session, company_id: int) -> int:
    try:
        start, next_start = _month_bounds(date.today())
        used = db.execute(
            text(
                """
                SELECT COALESCE(SUM(count), 0)
                FROM api_metrics_daily
                WHERE company_id = :cid
                  AND day >= :start AND day < :next_start
                """
            ),
            {"cid": company_id, "start": start, "next_start": next_start},
        ).scalar()
        return int(used or 0)
    except Exception:
        return 0

def _increment_daily(db: Session, *, company_id: int, endpoint: str, package_id: Optional[int] = None) -> int:
    """
    Povećaj dnevni brojač. Ako tablica ne postoji, tiho preskoči (vrati 0).
    """
    try:
        day = date.today().strftime("%Y-%m-%d")
        found = db.execute(
            text(
                """
                SELECT id, count
                FROM api_metrics_daily
                WHERE company_id = :cid AND endpoint = :ep AND day = :day
                  AND ((package_id IS NULL AND :pid IS NULL) OR package_id = :pid)
                """
            ),
            {"cid": company_id, "ep": endpoint, "day": day, "pid": package_id},
        ).mappings().first()

        if found:
            db.execute(text("UPDATE api_metrics_daily SET count = count + 1 WHERE id = :id"), {"id": found["id"]})
            return int(found["count"]) + 1

        db.execute(
            text(
                """
                INSERT INTO api_metrics_daily(company_id, package_id, endpoint, count, day, created_at)
                VALUES (:cid, :pid, :ep, 1, :day, datetime('now'))
                """
            ),
            {"cid": company_id, "pid": package_id, "ep": endpoint, "day": day},
        )
        return 1
    except Exception:
        return 0

def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Putanje koje ne želimo brojati (ako bi dependency ipak bio aktiviran)
IGNORED_PATH_PREFIXES = (
    "/api/healthz",
    "/api/readyz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/login",
)

# -----------------------------
# Dependency (pre-check + post-log)
# -----------------------------
async def meter_api(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    - Ako je METERING_ENABLED=false → dependency je no-op.
    - Prije rute: provjeri mjesečnu kvotu i (ako je definirana) vrati 429 kad je potrošena.
    - Nakon rute: broj samo uspješne odgovore (status < 400), inkrement dnevnog brojača.
    - SuperAdmin: nema ograničenja ni brojanja.
    - Dodaj X-RateLimit-* header-e kada je kvota definirana.
    """
    # Kill-switch
    if not METERING_ENABLED:
        yield
        return

    # Bypass: SuperAdmin nije ograničen niti ga brojimo
    if bool(getattr(current_user, "is_super_admin", False)):
        yield
        return

    company_id = getattr(current_user, "company_id", None)
    if not company_id:
        # Nema company scopa – pusti i ne broj
        yield
        return

    # Skip tehničke rute i OPTIONS
    path = request.url.path
    if request.method.upper() == "OPTIONS" or any(path.startswith(p) for p in IGNORED_PATH_PREFIXES):
        yield
        return

    # 1) PRE-CHECK KVOTE (sigurni fallbackovi unutra)
    package_id, quota = _find_company_package(db, company_id)
    used_before = _current_month_usage(db, company_id) if quota else 0
    reset_at = _month_reset_iso() if quota else None

    if quota and used_before >= quota:
        # Audit 429 (best-effort)
        try:
            audit_log(
                db,
                company_id=company_id,
                user_id=getattr(current_user, "id", None),
                action="API_QUOTA_EXCEEDED",
                entity_type="metering",
                entity_id=None,
                meta={"endpoint": path, "quota_month": quota, "used": used_before},
                ip=ip_from_request(request),
            )
            db.commit()
        except Exception:
            db.rollback()

        headers = {
            "X-RateLimit-Limit": str(quota),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": reset_at or "",
        }
        raise HTTPException(status_code=429, detail="Monthly API quota exceeded for this company", headers=headers)

    # 2) IZVRŠI RUTU
    try:
        yield
    finally:
        # 3) POST-LOG HIT samo za uspješne odgovore
        try:
            successful = 200 <= int(getattr(response, "status_code", 200)) < 400
            if successful:
                new_count = _increment_daily(db, company_id=company_id, endpoint=path, package_id=package_id)
                if quota:
                    # Pretpostavi +1 (jeftinije nego ponovno čitanje)
                    used_after = used_before + 1
                    remaining_after = max(quota - used_after, 0)
                    response.headers["X-RateLimit-Limit"] = str(quota)
                    response.headers["X-RateLimit-Remaining"] = str(remaining_after)
                    response.headers["X-RateLimit-Reset"] = reset_at or ""
        except Exception:
            # nikad ne rušiti poslovnu operaciju zbog meteringa
            pass