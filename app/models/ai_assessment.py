# app/models/ai_assessment.py
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Index,        # <-- potrebno za indekse
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base  # isti Base kao i ostali modeli


class AIAssessment(Base):
    __tablename__ = "ai_assessments"

    id = Column(Integer, primary_key=True, index=True)

    # FK redoslijed: users, companies, ai_systems se kreiraju prije u main.py
    ai_system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id   = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by   = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # snapshot procjene
    answers_json     = Column(Text, nullable=False)   # serijalizirani odgovori (JSON kao tekst)
    risk_tier        = Column(String(50), nullable=True)
    prohibited       = Column(Boolean, default=False, nullable=False)
    high_risk        = Column(Boolean, default=False, nullable=False)
    obligations_json = Column(Text, nullable=True)    # serijalizirane obveze (JSON kao tekst)

    # verzioniranje po vremenu (najnovija = latest)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # relacije (opcionalno, bez back_populates da izbjegnemo cikluse)
    system  = relationship("AISystem", lazy="joined")
    company = relationship("Company", lazy="joined")
    author  = relationship("User", lazy="joined")


# Indeksi (traženje "zadnje procjene" po sustavu/kompaniji)
Index(
    "ix_ai_assessments_system_created",
    AIAssessment.ai_system_id,
    AIAssessment.created_at.desc(),
)

Index(
    "ix_ai_assessments_company_created",
    AIAssessment.company_id,
    AIAssessment.created_at.desc(),
)