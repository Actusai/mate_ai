from app.db.session import SessionLocal
from app.models.package import Package

AR_PACKAGES = [
    {
        "name": "AR Starter (1/1)",
        "description": "All features. 1 client, 1 user.",
        "price": 150.0,
        "ai_system_limit": 0,
        "user_limit": 1,
        "client_limit": 1,
        "is_ar_only": 1,
    },
    {
        "name": "AR Basic",
        "description": "All features. 5 clients, 3 users (AR + 2).",
        "price": 500.0,
        "ai_system_limit": 0,
        "user_limit": 3,
        "client_limit": 5,
        "is_ar_only": 1,
    },
    {
        "name": "AR Standard",
        "description": "All features. 10 clients, 7 users.",
        "price": 1000.0,
        "ai_system_limit": 0,
        "user_limit": 7,
        "client_limit": 10,
        "is_ar_only": 1,
    },
    {
        "name": "AR Pro",
        "description": "All features. 25 clients, 15 users.",
        "price": 2500.0,
        "ai_system_limit": 0,
        "user_limit": 15,
        "client_limit": 25,
        "is_ar_only": 1,
    },
    {
        "name": "AR Enterprise",
        "description": "All features. Unlimited clients & team. Custom pricing.",
        "price": 0.0,   # custom
        "ai_system_limit": 0,
        "user_limit": 0,     # 0 = unlimited
        "client_limit": 0,   # 0 = unlimited
        "is_ar_only": 1,
    },
]

def seed_ar_packages():
    db = SessionLocal()
    created = 0
    try:
        for data in AR_PACKAGES:
            existing = db.query(Package).filter(Package.name == data["name"]).first()
            if not existing:
                db.add(Package(**data))
                created += 1
        db.commit()
        print(f"✅ AR packages seeded (+{created} new).")
    finally:
        db.close()

if __name__ == "__main__":
    seed_ar_packages()
