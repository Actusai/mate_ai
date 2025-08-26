# app/main.py
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import OAuth2PasswordBearer

from app.db.session import engine

# --- MODELS ---
# VAŽNO: redoslijed import-a prati ovisnosti FK:
# users -> companies -> packages -> company_package -> invites -> password_reset
# ai_systems (FK -> companies/users) -> admin_assignment (FK -> users/companies)
# system_assignment (FK -> users/ai_systems)
from app.models import user, company, package, company_package, invite
from app.models import password_reset, ai_system, admin_assignment
from app.models import system_assignment  # ovisi o ai_system + user

# --- ROUTERS ---
from app.api.v1 import auth, invites, catalog, users
from app.api.v1 import passwords_public, passwords
from app.api.v1 import companies as companies_api
from app.api.v1 import admin_assignments
from app.api.v1 import systems
from app.api.v1 import system_assignments

# --- CREATE TABLES (idempotentno; redoslijed zbog FK) ---
user.Base.metadata.create_all(bind=engine)               # users
company.Base.metadata.create_all(bind=engine)            # companies
package.Base.metadata.create_all(bind=engine)            # packages
company_package.Base.metadata.create_all(bind=engine)    # company_packages
invite.Base.metadata.create_all(bind=engine)             # invites
password_reset.Base.metadata.create_all(bind=engine)     # password_resets

ai_system.Base.metadata.create_all(bind=engine)          # ai_systems
admin_assignment.Base.metadata.create_all(bind=engine)   # admin_assignments
system_assignment.Base.metadata.create_all(bind=engine)  # system_assignments

app = FastAPI(title="Mate AI")

# --- MOUNT ROUTERS ---
app.include_router(auth.router,               prefix="/api/v1", tags=["auth"])
app.include_router(passwords_public.router,   prefix="/api/v1", tags=["auth"])
app.include_router(passwords.router,          prefix="/api/v1", tags=["auth"])
app.include_router(invites.router,            prefix="/api/v1", tags=["invites"])
app.include_router(catalog.router,            prefix="/api/v1", tags=["catalog"])
app.include_router(users.router,              prefix="/api/v1", tags=["users"])
app.include_router(companies_api.router,      prefix="/api/v1", tags=["companies"])
app.include_router(admin_assignments.router,  prefix="/api/v1", tags=["admin_assignments"])
app.include_router(systems.router,            prefix="/api/v1", tags=["systems"])
app.include_router(system_assignments.router, prefix="/api/v1", tags=["system_assignments"])

# --- OpenAPI security scheme (samo schema; ne forsira globalno) ---
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
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi