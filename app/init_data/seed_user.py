from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.user import User
from app.models.company import Company  # ensure Company table is known to metadata
from app.core.security import get_password_hash

# Make sure tables exist
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Ensure there is at least one company to attach the user to
company = db.query(Company).filter(Company.id == 1).first()
if not company:
    company = Company(id=1, name="Test Company", address="N/A", country="N/A", email="contact@testco.example")
    db.add(company)
    db.commit()
    db.refresh(company)

# Create test user if not exists
existing_user = db.query(User).filter(User.email == "admin@example.com").first()

if not existing_user:
    user = User(
        email="admin@example.com",
        hashed_password=get_password_hash("test123"),
        company_id=company.id,
        role="admin",
    )
    db.add(user)
    db.commit()
    print("Test user created.")
else:
    print("User already exists.")

db.close()
