# app/main.py
from fastapi import FastAPI, Depends, Request
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from fastapi.security import OAuth2PasswordBearer

# --- SCHEDULER ---
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal, engine

# --- MODELS (order matters due to FKs) ---
# users -> companies -> packages -> company_package -> invites -> password_reset
# ai_systems -> admin_assignment -> system_assignment -> ai_assessment
from app.models import user, company, package, company_package, invite
from app.models import password_reset, ai_system, admin_assignment
from app.models import system_assignment
from app.models import ai_assessment
from app.models import task_stats
from app.models import compliance_task
from app.models import notification
from app.models import incident
from app.models import assessment_approval

# --- ROUTERS ---
from app.api.v1 import auth, invites, catalog, users
from app.api.v1 import passwords_public, passwords
from app.api.v1 import companies as companies_api
from app.api.v1 import admin_assignments
from app.api.v1 import systems
from app.api.v1 import system_assignments
from app.api.v1 import me
from app.api.v1 import dashboard
from app.api.v1 import assessments
from app.api.v1 import compliance_tasks
from app.api.v1 import packages as packages_api
from app.api.v1 import company_packages as company_packages_api
from app.api.v1 import reports
from app.api.v1 import audit_logs
from app.api.v1 import notifications as notifications_api
from app.api import health as health_api

# --- SERVICES / DEPENDENCIES ---
from app.services.metering import meter_api
from app.services.snapshots import run_snapshots
from app.services.notifications import run_notifications_cycle

# --- ERROR HANDLERS & LOGGING ---
from app.core.errors import register_exception_handlers
import logging, time, uuid

#---- Documents ---
from app.api.v1 import documents
from app.models import document

#---- Incidents ---
from app.api.v1 import incidents

# --- CREATE TABLES (idempotent; order matters) ---
user.Base.metadata.create_all(bind=engine)               # users
company.Base.metadata.create_all(bind=engine)            # companies
package.Base.metadata.create_all(bind=engine)            # packages
company_package.Base.metadata.create_all(bind=engine)    # junction
invite.Base.metadata.create_all(bind=engine)             # invites
password_reset.Base.metadata.create_all(bind=engine)     # password_resets

ai_system.Base.metadata.create_all(bind=engine)          # ai_systems
admin_assignment.Base.metadata.create_all(bind=engine)   # admin_assignments
system_assignment.Base.metadata.create_all(bind=engine)  # system_assignments
ai_assessment.Base.metadata.create_all(bind=engine)      # ai_assessments

task_stats.Base.metadata.create_all(bind=engine)         # snapshots (TaskStatsDaily, OwnerTaskStatsDaily)
compliance_task.Base.metadata.create_all(bind=engine)    # compliance_tasks
notification.Base.metadata.create_all(bind=engine)       # notifications
document.Base.metadata.create_all(bind=engine)         # documents
incident.Base.metadata.create_all(bind=engine)         # incidents
assessment_approval.Base.metadata.create_all(bind=engine)  # assessment_approvals

# --- APP ---
def generate_unique_id(route: APIRoute) -> str:
    """Generate a stable unique operationId from: tag + HTTP method + path."""
    method = sorted(route.methods)[0].lower() if route.methods else "get"
    tag = (route.tags[0] if route.tags else "default").lower()
    path = route.path.replace("/", "_").replace("{", "").replace("}", "")
    return f"{tag}.{method}{path}".strip("._")

app = FastAPI(
    title="Mate AI",
    generate_unique_id_function=generate_unique_id,
)

# Register global exception handlers
register_exception_handlers(app)

# Basic logging configuration (adjust externally as needed)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
req_log = logging.getLogger("app.requests")

# Request logging middleware (+ correlation headers and duration)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()

    # Prefer inbound correlation headers if present
    inbound_id = (
        request.headers.get("X-Request-ID")
        or request.headers.get("X-Correlation-ID")
        or request.headers.get("X-Trace-ID")
    )
    rid = inbound_id or str(uuid.uuid4())
    # Store under common names for error handlers and downstream use
    request.state.request_id = rid
    request.state.trace_id = rid

    client_ip = request.client.host if request.client else "-"
    auth_hdr = request.headers.get("authorization")
    auth = "yes" if auth_hdr else "no"
    path = request.url.path
    method = request.method

    # These may be populated by get_current_user during dependency resolution
    user_id = getattr(request.state, "user_id", None)
    company_id = getattr(request.state, "company_id", None)
    is_super = getattr(request.state, "is_super_admin", None)

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        status_code = 500
        # Re-raise so our global exception handlers craft the JSON error
        raise
    finally:
        dur_ms = (time.perf_counter() - start) * 1000.0
        # Attach correlation headers if a response object exists
        if "response" in locals():
            response.headers["X-Request-ID"] = rid
            response.headers["X-Correlation-ID"] = rid
            response.headers["X-Trace-ID"] = rid
            response.headers["X-Process-Time"] = f"{dur_ms:.2f}ms"

        # Refresh user context in case it was set after we read it (common with dependencies)
        if user_id is None:
            user_id = getattr(request.state, "user_id", None)
        if company_id is None:
            company_id = getattr(request.state, "company_id", None)
        if is_super is None:
            is_super = getattr(request.state, "is_super_admin", None)

        req_log.info(
            "method=%s path=%s status=%s dur_ms=%.2f ip=%s auth=%s rid=%s uid=%s cid=%s super=%s",
            method, path, status_code, dur_ms, client_ip, auth, rid,
            str(user_id) if user_id is not None else "-",
            str(company_id) if company_id is not None else "-",
            "yes" if is_super else "no",
        )
    return response

# =============================
# Background jobs (snapshots & notifications)
# =============================
scheduler: BackgroundScheduler | None = None

def _snapshot_job():
    db = SessionLocal()
    try:
        # Default: today's snapshot for all companies
        run_snapshots(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def _notifications_job():
    db = SessionLocal()
    try:
        # Run for all companies (None); can iterate per company if needed
        run_notifications_cycle(db, company_id=None)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@app.on_event("startup")
def _start_scheduler():
    global scheduler
    if scheduler is None:
        scheduler = BackgroundScheduler(timezone=ZoneInfo("Europe/Zagreb"))
        # Daily snapshots at 02:15 local time
        scheduler.add_job(
            _snapshot_job,
            CronTrigger(hour=2, minute=15),
            id="daily_snapshots",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        # Daily due-task notifications at 08:30 local time
        scheduler.add_job(
            _notifications_job,
            CronTrigger(hour=8, minute=30),
            id="daily_notifications",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        scheduler.start()

@app.on_event("shutdown")
def _stop_scheduler():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None

# --- MOUNT ROUTERS ---
# Public (no auth / no metering)
app.include_router(auth.router,             prefix="/api/v1", tags=["auth"])
app.include_router(passwords_public.router, prefix="/api/v1", tags=["auth"])
app.include_router(passwords.router,        prefix="/api/v1", tags=["auth"])
app.include_router(invites.router,          prefix="/api/v1", tags=["invites"])
app.include_router(catalog.router,          prefix="/api/v1", tags=["catalog"])

# Protected (RBAC) + API metering
app.include_router(users.router,                prefix="/api/v1", tags=["users"],               dependencies=[Depends(meter_api)])
app.include_router(companies_api.router,        prefix="/api/v1", tags=["companies"],           dependencies=[Depends(meter_api)])
app.include_router(admin_assignments.router,    prefix="/api/v1", tags=["admin_assignments"],   dependencies=[Depends(meter_api)])
app.include_router(systems.router,              prefix="/api/v1", tags=["systems"],             dependencies=[Depends(meter_api)])
app.include_router(system_assignments.router,   prefix="/api/v1", tags=["system_assignments"],  dependencies=[Depends(meter_api)])
app.include_router(me.router,                   prefix="/api/v1", tags=["me"],                  dependencies=[Depends(meter_api)])
app.include_router(dashboard.router,            prefix="/api/v1", tags=["dashboard"],           dependencies=[Depends(meter_api)])
app.include_router(assessments.router,          prefix="/api/v1", tags=["assessments"],         dependencies=[Depends(meter_api)])
app.include_router(compliance_tasks.router,     prefix="/api/v1", tags=["compliance_tasks"],    dependencies=[Depends(meter_api)])
app.include_router(packages_api.router,         prefix="/api/v1", tags=["packages"],            dependencies=[Depends(meter_api)])
app.include_router(company_packages_api.router, prefix="/api/v1", tags=["company-packages"],    dependencies=[Depends(meter_api)])
app.include_router(reports.router,              prefix="/api/v1", tags=["reports"],             dependencies=[Depends(meter_api)])
app.include_router(audit_logs.router,           prefix="/api/v1", tags=["audit"],               dependencies=[Depends(meter_api)])
app.include_router(notifications_api.router,    prefix="/api/v1", tags=["notifications"],       dependencies=[Depends(meter_api)])
app.include_router(documents.router,            prefix="/api/v1", tags=["documents"],           dependencies=[Depends(meter_api)])
app.include_router(incidents.router,            prefix="/api/v1", tags=["incidents"],           dependencies=[Depends(meter_api)])

# Health endpoints (no version prefix)
app.include_router(health_api.router, prefix="/api")

# --- OpenAPI security scheme (schema only; not enforced globally) ---
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
        "flows": {"password": {"tokenUrl": "/api/v1/login", "scopes": {}}}
    }

    # Fallback de-dup operationId
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
                safe_tag = "".join(c for c in tag.lower().replace(" ", "_") if c.isalnum() or c in {"_", "-"})
                # use 'ch' variable when building suffix (bugfix)
                path_suffix = "".join(ch for ch in path.replace("/", "_") if ch.isalnum() or ch in {"_", "-"})
                new_id = f"{op_id}_{safe_tag}_{m}_{path_suffix}"
                counter = 2
                while new_id in seen:
                    new_id = f"{op_id}_{safe_tag}_{m}_{path_suffix}_{counter}"
                    counter += 1
                operation["operationId"] = new_id
                seen[new_id] = (path, method)
            else:
                seen[op_id] = (path, method)

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi