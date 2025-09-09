# app/models/compliance_task.py
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, ForeignKey,
    CheckConstraint, Index
)
from sqlalchemy.orm import relationship

from app.db.base import Base          # ⬅️ izbjegni kružni import Base-a
from app.models.user import User
from app.models.company import Company
from app.models.ai_system import AISystem

# NOTE: keep simple string "enums" for SQLite portability
TASK_STATUS = ("open", "in_progress", "blocked", "postponed", "done")
TASK_SEVERITY = ("mandatory", "recommended")  # add "nice_to_have" later if needed


class ComplianceTask(Base):
    __tablename__ = "compliance_tasks"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    ai_system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="SET NULL"), nullable=True, index=True)

    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # open | in_progress | blocked | postponed | done
    status = Column(String(20), nullable=False, default="open", index=True)

    # mandatory | recommended
    severity = Column(String(20), nullable=False, default="mandatory")

    # explicit, handy for filters
    mandatory = Column(Boolean, nullable=False, default=True)

    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Deadlines / completion
    due_date = Column(DateTime, nullable=True, index=True)
    completed_at = Column(DateTime, nullable=True)

    # Evidence / notes
    evidence_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # Reference to a legal or internal requirement (AI Act / GDPR / internal policy)
    reference = Column(String(255), nullable=True, index=True)

    # Days before due_date to send reminder (notification job uses this)
    reminder_days_before = Column(Integer, nullable=True, default=7)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    company = relationship(Company)
    system = relationship(AISystem)
    owner = relationship(User, foreign_keys=[owner_user_id])
    creator = relationship(User, foreign_keys=[created_by], viewonly=True)
    updater = relationship(User, foreign_keys=[updated_by], viewonly=True)

    __table_args__ = (
        # Validate allowed values (works on SQLite too)
        CheckConstraint(
            f"status IN {TASK_STATUS}",
            name="ck_compliance_tasks_status_allowed",
        ),
        CheckConstraint(
            f"severity IN {TASK_SEVERITY}",
            name="ck_compliance_tasks_severity_allowed",
        ),
        # Helpful composite indexes for common filters
        Index("ix_tasks_company_status", "company_id", "status"),
        Index("ix_tasks_system_status", "ai_system_id", "status"),
        Index("ix_tasks_owner_status", "owner_user_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<ComplianceTask id={self.id} title={self.title!r} status={self.status} due={self.due_date}>"