# app/main.py
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv, find_dotenv

# ---------------------------
# Env loading (root .env first, then app/.env as fallback)
# ---------------------------
# 1) root .env (if present)
load_dotenv(find_dotenv(usecwd=True))
# 2) app/.env (do not override values already loaded)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# --- DB engine (must be imported BEFORE create_all) ---
try:
    from app.db.session import engine
except Exception:
    # Fallback to avoid hard crash if session export changes
    from sqlalchemy import create_engine

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    connect_args = (
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    )
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

# --- SCHEDULER (optional) ---
try:
    from app.worker.scheduler import (
        make_scheduler,
    )  # factory that wires APScheduler jobs
except Exception:
    make_scheduler = None  # type: ignore

# ---------------------------
# MODELS (order matters due to FKs)
# ---------------------------
from app.models import user, company, package, company_package, invite
from app.models import password_reset, ai_system, admin_assignment, system_assignment
from app.models import ai_assessment

# Optional models (import safely; skip if missing). We also keep references so we can
# call create_all on their Base in case there is no unified DBBase.
_optional_model_names = (
    "incident",
    "notification",
    "document",
    "task_stats",
    "assessment_approval",
    "regulatory_deadline",
    "calendar_pin",  # calendar pins model/table
)
_optional_model_modules = []
for _mod in _optional_model_names:
    try:
        _m = __import__(f"app.models.{_mod}", fromlist=["Base"])
        _optional_model_modules.append(_m)
    except Exception:
        pass

# ---------------------------
# ROUTERS (core)
# ---------------------------
from app.api.v1 import auth, invites, catalog, users
from app.api.v1 import passwords_public, passwords
from app.api.v1 import companies as companies_api
from app.api.v1 import admin_assignments
from app.api.v1 import systems
from app.api.v1 import system_assignments
from app.api.v1 import me
from app.api.v1 import dashboard
from app.api.v1 import assessments

# Additional core routers we want mounted unconditionally
from app.api.v1 import compliance_tasks
from app.api.v1 import packages as packages_api
from app.api.v1 import company_packages as company_packages_api


# Optional routers (loaded only if modules exist)
def _try_import_router(module_path: str):
    try:
        mod = __import__(module_path, fromlist=["router"])
        return getattr(mod, "router", None)
    except Exception:
        return None


incidents_router = _try_import_router("app.api.v1.incidents")
notifications_router = _try_import_router("app.api.v1.notifications")
documents_router = _try_import_router("app.api.v1.documents")
reports_router = _try_import_router("app.api.v1.reports")
audit_logs_router = _try_import_router("app.api.v1.audit_logs")
health_router = _try_import_router("app.api.health")
fria_router = _try_import_router("app.api.v1.fria")
calendar_router = _try_import_router("app.api.v1.calendar")
calendar_pins_router = _try_import_router("app.api.v1.calendar_pins")

# ---------------------------
# CREATE TABLES (dev-only; guard with env)
# ---------------------------
ENABLE_CREATE_ALL = os.getenv("ENABLE_CREATE_ALL", "1") == "1"

if ENABLE_CREATE_ALL:
    user.Base.metadata.create_all(bind=engine)
    company.Base.metadata.create_all(bind=engine)
    package.Base.metadata.create_all(bind=engine)
    company_package.Base.metadata.create_all(bind=engine)
    invite.Base.metadata.create_all(bind=engine)
    password_reset.Base.metadata.create_all(bind=engine)

    ai_system.Base.metadata.create_all(bind=engine)
    admin_assignment.Base.metadata.create_all(bind=engine)
    system_assignment.Base.metadata.create_all(bind=engine)

    ai_assessment.Base.metadata.create_all(bind=engine)

    # If a unified Base exists (e.g., app.db.base.Base), also create_all on it
    try:
        from app.db.base import Base as DBBase

        DBBase.metadata.create_all(bind=engine)
    except Exception:
        pass

    # Ensure optional model tables are created even if DBBase doesn't exist
    for _m in _optional_model_modules:
        try:
            _base = getattr(_m, "Base", None)
            if _base is not None:
                _base.metadata.create_all(bind=engine)
        except Exception:
            # best-effort; don't crash app startup
            pass

# ---------------------------
# APP
# ---------------------------
app = FastAPI(title="Mate AI")

# ---------------------------
# ROUTER MOUNT
# ---------------------------
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(passwords_public.router, prefix="/api/v1", tags=["auth"])
app.include_router(passwords.router, prefix="/api/v1", tags=["auth"])
app.include_router(invites.router, prefix="/api/v1", tags=["invites"])
app.include_router(catalog.router, prefix="/api/v1", tags=["catalog"])
app.include_router(users.router, prefix="/api/v1", tags=["users"])
app.include_router(companies_api.router, prefix="/api/v1", tags=["companies"])
app.include_router(
    admin_assignments.router, prefix="/api/v1", tags=["admin_assignments"]
)
app.include_router(systems.router, prefix="/api/v1", tags=["systems"])
app.include_router(
    system_assignments.router, prefix="/api/v1", tags=["system_assignments"]
)
app.include_router(me.router, prefix="/api/v1", tags=["me"])
app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
app.include_router(assessments.router, prefix="/api/v1", tags=["assessments"])

# Core feature routers explicitly
app.include_router(compliance_tasks.router, prefix="/api/v1", tags=["compliance_tasks"])
app.include_router(packages_api.router, prefix="/api/v1", tags=["packages"])
app.include_router(
    company_packages_api.router, prefix="/api/v1", tags=["company_packages"]
)

# Optional routers (only if present)
if documents_router:
    app.include_router(documents_router, prefix="/api/v1", tags=["documents"])
if incidents_router:
    app.include_router(incidents_router, prefix="/api/v1", tags=["incidents"])
if notifications_router:
    app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
if reports_router:
    app.include_router(reports_router, prefix="/api/v1", tags=["reports"])
if audit_logs_router:
    app.include_router(audit_logs_router, prefix="/api/v1", tags=["audit"])
if health_router:
    app.include_router(health_router, prefix="/api", tags=["health"])
if fria_router:
    app.include_router(fria_router, prefix="/api/v1", tags=["fria"])
if calendar_router:
    app.include_router(calendar_router, prefix="/api/v1", tags=["calendar"])
if calendar_pins_router:
    app.include_router(calendar_pins_router, prefix="/api/v1", tags=["calendar"])


# ---------------------------
# Scheduler (daily reminders) – optional & safe
# ---------------------------
@app.on_event("startup")
def _start_scheduler():
    # Enable with ENABLE_SCHEDULER=1 (default 1). Time configured in worker/scheduler.py via env.
    if make_scheduler and os.getenv("ENABLE_SCHEDULER", "1") == "1":
        try:
            app.state.scheduler = make_scheduler()
            app.state.scheduler.start()
        except Exception:
            app.state.scheduler = None  # keep API running if scheduler fails


@app.on_event("shutdown")
def _stop_scheduler():
    sched = getattr(app.state, "scheduler", None)
    if sched:
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass


# ---------------------------
# OpenAPI (dedupe operationId)
# ---------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Mate AI",
        version="1.0.0",
        description="API for Mate AI platform",
        routes=app.routes,
    )

    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["OAuth2PasswordBearer"] = {
        "type": "oauth2",
        "flows": {"password": {"tokenUrl": "/api/v1/login", "scopes": {}}},
    }

    seen = {}
    for path, methods in openapi_schema.get("paths", {}).items():
        for method, operation in methods.items():
            m = method.lower()
            if m not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            op_id = operation.get("operationId")
            if not op_id:
                continue
            if op_id in seen:
                tag = (operation.get("tags") or [""])[0]
                safe_tag = "".join(
                    c
                    for c in tag.lower().replace(" ", "_")
                    if c.isalnum() or c in {"_", "-"}
                )
                # Use 'ch' as the loop var when building safe path suffix
                path_suffix = "".join(
                    ch
                    for ch in path.replace("/", "_")
                    if ch.isalnum() or ch in {"_", "-"}
                )
                new_id = f"{op_id}_{safe_tag}_{m}_{path_suffix}"
                n = 2
                while new_id in seen:
                    new_id = f"{op_id}_{safe_tag}_{m}_{path_suffix}_{n}"
                    n += 1
                operation["operationId"] = new_id
                seen[new_id] = (path, method)
            else:
                seen[op_id] = (path, method)

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
