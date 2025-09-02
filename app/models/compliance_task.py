# app/models/compliance_task.py
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import relationship

from app.models.user import Base, User
from app.models.company import Company
from app.models.ai_system import AISystem

# Napomena: koristimo string Enum radi portability-a u SQLite
TASK_STATUS = ("open", "in_progress", "blocked", "postponed", "done")
TASK_SEVERITY = ("mandatory", "recommended")  # možeš dodati "nice_to_have" kasnije

class ComplianceTask(Base):
    __tablename__ = "compliance_tasks"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    ai_system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # open | in_progress | blocked | postponed | done
    status = Column(String(20), nullable=False, default="open")

    # mandatory | recommended
    severity = Column(String(20), nullable=False, default="mandatory")

    # eksplicitno polje, zgodno za filtere
    mandatory = Column(Boolean, nullable=False, default=True)

    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Rokovi / završetak
    due_date = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Dokaz / bilješke
    evidence_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # NOVO: referenca na pravni temelj (AI Act/GDPR/interna politika)
    reference = Column(String(255), nullable=True)

    # NOVO: broj dana prije roka kada šaljemo reminder (backend logika)
    reminder_days_before = Column(Integer, nullable=True, default=7)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacije
    company = relationship(Company)
    system = relationship(AISystem)
    owner = relationship(User, foreign_keys=[owner_user_id])
    creator = relationship(User, foreign_keys=[created_by], viewonly=True)
    updater = relationship(User, foreign_keys=[updated_by], viewonly=True)

    def __repr__(self) -> str:
        return f"<ComplianceTask id={self.id} title={self.title!r} status={self.status} due={self.due_date}>"
