# app/api/health.py
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.auth import get_db
from datetime import datetime, timezone
import time

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    # Jednostavni liveness signal (uvijek 200 ako proces živi)
    return {
        "ok": True,
        "service": "mate_ai",
        "status": "healthy",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    # Readiness: brzi DB ping + latency
    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return JSONResponse(
            status_code=200,
            content={"ok": True, "db": "up", "db_latency_ms": round(latency_ms, 2)},
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"ok": False, "db": "down", "error": str(e)},
            headers={"Cache-Control": "no-store"},
        )
