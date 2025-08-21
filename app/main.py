from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.security import OAuth2PasswordBearer

from app.db.session import engine
from app.models import user, company, package, company_package, invite
from app.api.v1 import auth, invites, catalog
from app.api.v1 import users  # ⬅️ NOVO

# Kreiraj tablice (ostaje jednostavno za SQLite demo)
user.Base.metadata.create_all(bind=engine)
company.Base.metadata.create_all(bind=engine)
package.Base.metadata.create_all(bind=engine)
company_package.Base.metadata.create_all(bind=engine)
invite.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mate AI")

# Routers
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(invites.router, prefix="/api/v1", tags=["invites"])
app.include_router(catalog.router, prefix="/api/v1", tags=["catalog"])
app.include_router(users.router, prefix="/api/v1", tags=["users"])  # ⬅️ NOVO

# OpenAPI security (da Swagger ima "Authorize" gumb)
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
    openapi_schema["components"]["securitySchemes"] = {
        "OAuth2PasswordBearer": {
            "type": "oauth2",
            "flows": {
                "password": {
                    "tokenUrl": "/api/v1/login",
                    "scopes": {}
                }
            }
        }
    }
    openapi_schema["security"] = [{"OAuth2PasswordBearer": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
