# app/models/admin_assignment.py
from sqlalchemy import Column, Integer, ForeignKey, UniqueConstraint, DateTime, func
from sqlalchemy.orm import relationship

# Koristimo isti Base kao i ostali modeli da dijelimo jedno metadata stablo
from app.models.user import Base, User
from app.models.company import Company


class AdminAssignment(Base):
    __tablename__ = "admin_assignments"

    id = Column(Integer, primary_key=True, index=True)

    # User koji je "interni admin" tvoje aplikacije (role='admin')
    admin_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Company (klijent) za kojeg je taj admin zadužen
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relacije (korisno za dohvat)
    admin = relationship("User", backref="admin_companies", foreign_keys=[admin_id])
    company = relationship("Company", backref="assigned_admins", foreign_keys=[company_id])

    # Sprječava duplikate istog para (admin, company)
    __table_args__ = (
        UniqueConstraint("admin_id", "company_id", name="uq_admin_company"),
    )