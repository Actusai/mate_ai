from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./mate.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

# Enable SQLite foreign key constraint enforcement
@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
