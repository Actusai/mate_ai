# app/db/session.py
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# SQLite DB (relative file ./mate.db)
DATABASE_URL = "sqlite:///./mate.db"

# Create engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + threads
    pool_pre_ping=True,  # safer reconnects
    future=True,
)


# Enforce foreign keys in SQLite
@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
)
