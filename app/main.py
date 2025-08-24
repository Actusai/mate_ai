from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import OAuth2PasswordBearer

from app.db.session import engine
from app.models import user, company, package, company_package, invite
from app.models import password_reset
from app.models import admin_assignment

# API routers
from app.api.v1 import auth, invites, catalog, users
from app.api.v1 import passwords_public, passwords
from app.api.v1 import companies as companies_api  
from app.api.v1 import admin_assignments

# --- Create tables (idempotentno) ---
user.Base.metadata.create_all(bind=engine)
company.Base.metadata.create_all(bind=engine)
package.Base.metadata.create_all(bind=engine)
company_package.Base.metadata.create_all(bind=engine)
invite.Base.metadata.create_all(bind=engine)
password_reset.Base.metadata.create_all(bind=engine)
admin_assignment.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mate AI")

# --- Routers ---
app.include_router(auth.router,              prefix="/api/v1", tags=["auth"])
app.include_router(passwords_public.router,  prefix="/api/v1", tags=["auth"])     # PUBLIC (forgot/reset)
app.include_router(passwords.router,         prefix="/api/v1", tags=["auth"])     # PROTECTED (change)
app.include_router(invites.router,           prefix="/api/v1", tags=["invites"])
app.include_router(catalog.router,           prefix="/api/v1", tags=["catalog"])
app.include_router(users.router,             prefix="/api/v1", tags=["users"])
app.include_router(companies_api.router,     prefix="/api/v1", tags=["companies"])  
app.include_router(admin_assignments.router, prefix="/api/v1", tags=["admin_assignments"])

# --- OpenAPI security scheme (samo schema; PUBLIC rute ostaju javne) ---
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
    # Ostavimo samo definiran security scheme bez globalnog enforcanja
    openapi_schema["components"]["securitySchemes"] = {
        "OAuth2PasswordBearer": {
            "type": "oauth2",
            "flows": {"password": {"tokenUrl": "/api/v1/login", "scopes": {}}}
        }
    }
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi