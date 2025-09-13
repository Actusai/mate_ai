# app/models/ai_assessment.py
import os
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base  # shared Base for all ORM models

# Toggle to safely map new approval columns only after Alembic migration is applied.
# Set ENABLE_ASSESSMENT_APPROVAL_COLUMNS=1 in the environment to enable.
ENABLE_APPROVAL_COLUMNS = os.getenv("ENABLE_ASSESSMENT_APPROVAL_COLUMNS", "0") == "1"


class AIAssessment(Base):
    __tablename__ = "ai_assessments"

    id = Column(Integer, primary_key=True, index=True)

    # FK order: users, companies, ai_systems are created earlier in main.py
    ai_system_id = Column(
        Integer,
        ForeignKey("ai_systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # assessment snapshot
    answers_json = Column(Text, nullable=False)  # serialized answers (JSON as text)
    risk_tier = Column(String(50), nullable=True)
    prohibited = Column(Boolean, default=False, nullable=False)
    high_risk = Column(Boolean, default=False, nullable=False)
    obligations_json = Column(
        Text, nullable=True
    )  # serialized obligations (JSON as text)

    # versioning by time (latest = most recent created_at)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- NEW (approval mirror on the assessment row) ---
    if ENABLE_APPROVAL_COLUMNS:
        approved_by = Column(
            Integer,
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )
        approved_at = Column(DateTime(timezone=True), nullable=True, index=True)
        approval_note = Column(Text, nullable=True)

    # relationships (explicit foreign_keys to avoid ambiguity once approved_by exists)
    system = relationship("AISystem", lazy="joined")
    company = relationship("Company", lazy="joined")
    author = relationship("User", foreign_keys=[created_by], lazy="joined")

    if ENABLE_APPROVAL_COLUMNS:
        approver = relationship("User", foreign_keys=[approved_by], lazy="joined")


# Indexes to speed up common lookups (latest per system/company)
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

# New composite indexes for approval timelines (activated only when columns are mapped)
if ENABLE_APPROVAL_COLUMNS:
    Index(
        "ix_ai_assessments_system_approved",
        AIAssessment.ai_system_id,
        AIAssessment.approved_at.desc(),
    )
    Index(
        "ix_ai_assessments_company_approved",
        AIAssessment.company_id,
        AIAssessment.approved_at.desc(),
    )
