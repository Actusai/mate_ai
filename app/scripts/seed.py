# app/scripts/seed.py

from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.user import User
from app.models.company import Company
from app.core.security import get_password_hash


def utcnow():
    return datetime.now(timezone.utc)


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


def _get_or_create_user(
    db, email: str, company: Company = None, role: str = "admin", password: str = "ChangeMe123!"
) -> User:
    u = db.query(User).filter(User.email == email).first()
    if u:
        return u
    u = User(
        email=email,
        hashed_password=get_password_hash(password),
        company_id=company.id if company else None,
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def run():
    db = SessionLocal()

    # Companies
    acme = _get_or_create_company(db, "Acme Corp")
    globex = _get_or_create_company(db, "Globex")

    # Admins & staff
    admin1 = _get_or_create_user(db, "admin@acme.test", acme, role="admin", password="AdminPass123")
    staff1 = _get_or_create_user(db, "staff@acme.test", acme, role="staff", password="StaffPass123")
    client1 = _get_or_create_user(db, "admin@globex.test", globex, role="admin", password="GlobexPass123")

    # Super admin (global, bez company_id)
    super_admin = _get_or_create_user(
        db, "superadmin@example.com", None, role="super_admin", password="SuperPass123"
    )

    # Staff koji radi za super admina
    sa_staff = _get_or_create_user(
        db, "sa.staff@yourco.com", None, role="staff", password="StaffOfSuper123"
    )

    print("Seed done. Users created/ensured:")
    print(f" - {admin1.email} / AdminPass123")
    print(f" - {staff1.email} / StaffPass123")
    print(f" - {client1.email} / GlobexPass123")
    print(f" - {super_admin.email} / SuperPass123")
    print(f" - {sa_staff.email} / StaffOfSuper123")


if __name__ == "__main__":
    run()