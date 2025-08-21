from app.db.session import SessionLocal
from app.models.package import Package
from app.db.base import Base
from app.db.session import engine

# Ensure tables are created
Base.metadata.create_all(bind=engine)

# Define package list
packages = [
    {
        "name": "Basic",
        "description": "1 AI system, no additional team members",
        "price": 0,
        "ai_system_limit": 1,
        "user_limit": 1
    },
    {
        "name": "Standard",
        "description": "Up to 3 AI systems and 3 team members",
        "price": 49,
        "ai_system_limit": 3,
        "user_limit": 3
    },
    {
        "name": "Pro",
        "description": "Up to 10 AI systems and 10 team members, advanced features",
        "price": 99,
        "ai_system_limit": 10,
        "user_limit": 10
    },
    {
        "name": "Enterprise",
        "description": "Unlimited AI systems and users, includes all features and SLA",
        "price": 199,
        "ai_system_limit": -1,  # -1 = unlimited
        "user_limit": -1
    }
]

db = SessionLocal()

for p in packages:
    exists = db.query(Package).filter(Package.name == p["name"]).first()
    if not exists:
        new_package = Package(**p)
        db.add(new_package)

db.commit()
db.close()

print("Packages successfully inserted.")
