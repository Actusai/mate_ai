from sqlalchemy import text


def _has_column(db, table: str, col: str) -> bool:
    try:
        cols = db.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return any((c.get("name") == col) for c in cols)
    except Exception:
        return False


def seed_packages(db):
    has_created_at = _has_column(db, "packages", "created_at")

    items = [
        {"code": "basic", "name": "Basic", "price_month": 49, "price_year": 490},
        {"code": "pro", "name": "Pro", "price_month": 149, "price_year": 1490},
        {
            "code": "enterprise",
            "name": "Enterprise",
            "price_month": 499,
            "price_year": 4990,
        },
    ]

    for p in items:
        row = db.execute(
            text("SELECT id FROM packages WHERE code = :code"), {"code": p["code"]}
        ).fetchone()

        if row:
            db.execute(
                text(
                    """
                    UPDATE packages
                    SET name = :name,
                        price_month = :pm,
                        price_year  = :py
                    WHERE id = :id
                """
                ),
                {
                    "id": row[0],
                    "name": p["name"],
                    "pm": p["price_month"],
                    "py": p["price_year"],
                },
            )
        else:
            if has_created_at:
                db.execute(
                    text(
                        """
                        INSERT INTO packages(code, name, price_month, price_year, created_at)
                        VALUES (:code, :name, :pm, :py, datetime('now'))
                    """
                    ),
                    {
                        "code": p["code"],
                        "name": p["name"],
                        "pm": p["price_month"],
                        "py": p["price_year"],
                    },
                )
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO packages(code, name, price_month, price_year)
                        VALUES (:code, :name, :pm, :py)
                    """
                    ),
                    {
                        "code": p["code"],
                        "name": p["name"],
                        "pm": p["price_month"],
                        "py": p["price_year"],
                    },
                )

    db.commit()
