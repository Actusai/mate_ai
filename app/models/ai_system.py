# app/models/ai_system.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship, declarative_base

# Koristimo isti Base kao i ostali modeli
from app.models.user import Base  # Base je već definiran u user.py
from app.models.company import Company
from app.models.user import User


class AISystem(Base):
    __tablename__ = "ai_systems"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)

    # osnovna meta polja
    name = Column(String(255), nullable=False, index=True)
    purpose = Column(Text, nullable=True)             # opis/namjena sustava
    lifecycle_stage = Column(String(50), nullable=True)  # npr. "development", "production"
    risk_tier = Column(String(50), nullable=True)     # npr. "prohibited", "high", "limited", "minimal"
    status = Column(String(50), nullable=True)        # npr. "active", "paused", "retired"
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # odgovorna osoba

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # relacije (opcionalno, ali korisno)
    company = relationship(Company, backref="ai_systems")
    owner = relationship(User, foreign_keys=[owner_user_id])