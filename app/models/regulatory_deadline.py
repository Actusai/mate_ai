# app/models/regulatory_deadline.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import relationship

# Use the same Base as other models so metadata is unified
from app.models.user import Base
from app.models.company import Company
from app.models.ai_system import AISystem
from app.models.user import User


class RegulatoryDeadline(Base):
    """
    Company- or system-scoped regulatory deadline (e.g., AI Act milestones).
    Designed to back the dashboard timeline and deadline reminders.
    """

    __tablename__ = "regulatory_deadlines"

    id = Column(Integer, primary_key=True, index=True)

    # Scope
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ai_system_id = Column(
        Integer,
        ForeignKey("ai_systems.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Metadata
    name = Column(
        String(255), nullable=False, index=True
    )  # e.g., "AI Act conformity deadline"
    kind = Column(
        String(50), nullable=True, index=True
    )  # e.g., "ai_act_general", "registration", ...
    due_date = Column(DateTime(timezone=True), nullable=False, index=True)

    # Optional attributes
    severity = Column(
        String(20), nullable=True, index=True
    )  # e.g., low|medium|high|critical
    status = Column(
        String(20), nullable=False, default="open", index=True
    )  # open|done|missed|waived|archived
    notes = Column(Text, nullable=True)  # free-form notes
    created_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships (lazy joined for efficient listings)
    company = relationship(Company, lazy="joined")
    system = relationship(AISystem, lazy="joined")
    author = relationship(User, foreign_keys=[created_by], lazy="joined")


# Helpful composite indexes for common queries
Index(
    "ix_regdl_company_due", RegulatoryDeadline.company_id, RegulatoryDeadline.due_date
)
Index(
    "ix_regdl_system_due", RegulatoryDeadline.ai_system_id, RegulatoryDeadline.due_date
)
Index("ix_regdl_kind_due", RegulatoryDeadline.kind, RegulatoryDeadline.due_date)
