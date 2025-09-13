# app/models/assessment_approval.py
from __future__ import annotations
import os
from sqlalchemy import Column, Integer, String, DateTime, Index, UniqueConstraint, func

# Use the shared Base so metadata is unified across models
from app.db.base import Base

# Toggle to enforce exactly one approval per assessment (enable via env + Alembic)
ENABLE_SINGLE_APPROVAL = os.getenv("ENABLE_SINGLE_ASSESSMENT_APPROVAL", "0") == "1"


class AssessmentApproval(Base):
    """
    Stores a single approval action for an AI assessment.
    FK constraints intentionally deferred to Alembic migration (same approach as incidents).
    """

    __tablename__ = "assessment_approvals"

    # Optional uniqueness (enable after running Alembic migration)
    if ENABLE_SINGLE_APPROVAL:
        __table_args__ = (
            UniqueConstraint("assessment_id", name="uq_assessment_approval_once"),
        )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # FKs added by Alembic later:
    #   assessment_id -> ai_assessments.id
    #   approver_user_id -> users.id
    assessment_id = Column(Integer, nullable=False, index=True)
    approver_user_id = Column(Integer, nullable=False, index=True)

    note = Column(String(1000), nullable=True)

    # Timestamps
    approved_at = Column(DateTime, nullable=False, server_default=func.now())
    created_at = Column(DateTime, nullable=False, server_default=func.now())


# Helpful indexes
Index("ix_assessment_approvals_assessment", AssessmentApproval.assessment_id)
Index("ix_assessment_approvals_approver", AssessmentApproval.approver_user_id)
Index(
    "ix_assessment_approvals_assessment_created",
    AssessmentApproval.assessment_id,
    AssessmentApproval.created_at.desc(),
)
