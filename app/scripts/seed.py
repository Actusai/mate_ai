# app/scripts/seed.py  (relevantni dijelovi)

from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.user import User
from app.models.company import Company

# pokušaj koristiti postojeći hash helper; ako ga nema, fallback
try:
    from app.core.security import get_password_hash
except Exception:
    import hashlib
    def get_password_hash(p: str) -> str:
        # Fallback: nije za produkciju, dovoljno za seed
        return hashlib.sha256(p.encode("utf-8")).hexdigest()

def utcnow():
    return datetime.now(timezone.utc)

def _get_or_create_user(db, email: str, company: Company, role: str = "admin", password: str = "ChangeMe123!"):
    u = db.query(User).filter(User.email == email).first()
    if u:
        return u
    u = User(
        email=email,
        hashed_password=get_password_hash(password),
        company_id=company.id,
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u

def _get_or_create_company(db, name: str) -> Company:
    c = db.query(Company).filter(Company.name == name).first()
    if c:
        return c
    c = Company(
        name=name,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c

def run():
    db = SessionLocal()

    # primjeri
    acme = _get_or_create_company(db, "Acme Corp")
    globex = _get_or_create_company(db, "Globex")

    admin1 = _get_or_create_user(db, "admin@acme.test", acme, role="admin")
    staff1 = _get_or_create_user(db, "staff@acme.test", acme, role="staff")
    client1 = _get_or_create_user(db, "admin@globex.test", globex, role="admin")

    print("Seed done.")

if __name__ == "__main__":
    run()