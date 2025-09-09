# app/models/user.py
from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DateTime,
    Boolean,
    text,
)
from sqlalchemy.orm import relationship
from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # Osnovno
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

    # Tenancy / RBAC
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True)
    role = Column(String(50), default="member", index=True)
    is_super_admin = Column(Boolean, nullable=False, server_default=text("0"))

    # Profil / status
    full_name = Column(String(255), nullable=True)
    invite_status = Column(String(30), nullable=True, index=True)  # npr. "pending", "accepted"
    is_active = Column(Boolean, nullable=False, server_default=text("1"))

    # Sigurnost / login metrike
    failed_login_attempts = Column(Integer, nullable=False, server_default=text("0"))
    locked_until = Column(DateTime, nullable=True, index=True)
    last_login_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default=text("datetime('now')"))
    updated_at = Column(DateTime, nullable=False, server_default=text("datetime('now')"))

    # Relacije
    company = relationship("Company", backref="users", passive_deletes=True)