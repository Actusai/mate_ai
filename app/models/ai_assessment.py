from datetime import datetime
from sqlalchemy import Column, Integer, ForeignKey, Text, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.db.session import Base

class AIAssessment(Base):
    __tablename__ = "ai_assessments"

    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(Integer, index=True, nullable=False)

    # raw answers (JSON as text for SQLite MVP)
    answers_json = Column(Text, nullable=False, default="{}")

    # derived results
    risk_tier = Column(String(50), nullable=True)          # "prohibited" | "high_risk" | "limited_risk" | "minimal_risk"
    prohibited = Column(Boolean, default=False)
    high_risk = Column(Boolean, default=False)
    obligations_json = Column(Text, nullable=False, default="[]")  # list of obligation codes

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    system = relationship("AISystem", backref="assessments")