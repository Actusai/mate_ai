# app/main.py
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from fastapi.security import OAuth2PasswordBearer

from app.db.session import engine

# --- MODELS (redoslijed je bitan zbog FK ovisnosti) ---
# users -> companies -> packages -> company_package -> invites -> password_reset
# ai_systems (FK -> companies/users) -> admin_assignment (FK -> users/companies)
# system_assignment (FK -> users/ai_systems) -> ai_assessment (FK -> users/ai_systems, users)
from app.models import user, company, package, company_package, invite
from app.models import password_reset, ai_system, admin_assignment
from app.models import system_assignment
from app.models import ai_assessment  # kreira se tek nakon users/ai_systems

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
# (Ako imaš admin varijantu paketa, uključi je po potrebi)
# from app.api.v1 import packages as packages_admin

# --- CREATE TABLES (idempotentno; redoslijed zbog FK!) ---
user.Base.metadata.create_all(bind=engine)               # users
company.Base.metadata.create_all(bind=engine)            # companies
package.Base.metadata.create_all(bind=engine)            # packages
company_package.Base.metadata.create_all(bind=engine)    # junction
invite.Base.metadata.create_all(bind=engine)             # invites
password_reset.Base.metadata.create_all(bind=engine)     # password_resets

ai_system.Base.metadata.create_all(bind=engine)          # ai_systems  (FK -> companies/users)
admin_assignment.Base.metadata.create_all(bind=engine)   # admin_assignments (FK -> users/companies)
system_assignment.Base.metadata.create_all(bind=engine)  # system_assignments (FK -> users/ai_systems)

ai_assessment.Base.metadata.create_all(bind=engine)      # ai_assessments (FK -> users/ai_systems)

# --- APP ---
def generate_unique_id(route: APIRoute) -> str:
    """
    Stabilno generira unikatni operationId iz: tag + HTTP metoda + path.
    Time izbjegavamo FastAPI warninge o duplikatima.
    """
    method = sorted(route.methods)[0].lower() if route.methods else "get"
    tag = (route.tags[0] if route.tags else "default").lower()
    path = route.path.replace("/", "_").replace("{", "").replace("}", "")
    return f"{tag}.{method}{path}".strip("._")

app = FastAPI(
    title="Mate AI",
    generate_unique_id_function=generate_unique_id
)

# --- MOUNT ROUTERS ---
app.include_router(auth.router,               prefix="/api/v1", tags=["auth"])
app.include_router(passwords_public.router,   prefix="/api/v1", tags=["auth"])
app.include_router(passwords.router,          prefix="/api/v1", tags=["auth"])
app.include_router(invites.router,            prefix="/api/v1", tags=["invites"])

# ⚠️ VAŽNO: catalog router uključujemo SAMO JEDNOM.
# Preporuka: u app/api/v1/catalog.py postavi `router = APIRouter(prefix="/catalog", tags=["catalog"])`
# pa će rute biti /api/v1/catalog/companies i /api/v1/catalog/packages
app.include_router(catalog.router,            prefix="/api/v1", tags=["catalog"])

app.include_router(users.router,              prefix="/api/v1", tags=["users"])
app.include_router(companies_api.router,      prefix="/api/v1", tags=["companies"])
app.include_router(admin_assignments.router,  prefix="/api/v1", tags=["admin_assignments"])
app.include_router(systems.router,            prefix="/api/v1", tags=["systems"])
app.include_router(system_assignments.router, prefix="/api/v1", tags=["system_assignments"])
app.include_router(me.router,                 prefix="/api/v1", tags=["me"])
app.include_router(dashboard.router,          prefix="/api/v1", tags=["dashboard"])
app.include_router(assessments.router,        prefix="/api/v1", tags=["assessments"])
app.include_router(compliance_tasks.router,   prefix="/api/v1", tags=["compliance_tasks"])
app.include_router(packages_api.router,       prefix="/api/v1", tags=["packages"])
app.include_router(company_packages_api.router, prefix="/api/v1", tags=["company-packages"])
# Ne duplirati catalog.router niti companies router!

# --- OpenAPI security scheme (samo schema; ne forsira globalno) ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")

def custom_openapi():
    """
    Zadržavamo tvoj custom OpenAPI:
      - postavlja OAuth2PasswordBearer security scheme
      - fallback deduplikacija operationId (ako bi se igdje ipak potkrao duplikat)
    """
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Mate AI",
        version="1.0.0",
        description="API for Mate AI platform",
        routes=app.routes,
    )

    # Security scheme
    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["OAuth2PasswordBearer"] = {
        "type": "oauth2",
        "flows": {"password": {"tokenUrl": "/api/v1/login", "scopes": {}}}
    }

    # --- Fallback de-dup operationId (trebao bi rijetko biti potreban s generate_unique_id_function) ---
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