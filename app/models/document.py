# app/models/document.py
from __future__ import annotations
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship

# Reuse the shared Base (same pattern as other models)
from app.models.user import Base, User
from app.models.company import Company
from app.models.ai_system import AISystem
from app.models.compliance_task import ComplianceTask


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)

    # Scoping – keep explicit company_id for RBAC and fast filtering
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    # Optional links
    ai_system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="SET NULL"), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("compliance_tasks.id", ondelete="SET NULL"), nullable=True, index=True)

    # Metadata
    document_type = Column(String(50), nullable=False, default="evidence")  # e.g. evidence, policy, report, other
    version = Column(String(50), nullable=True)
    effective_date = Column(Date, nullable=True)

    # For now we only store an external URL (file uploads can come later)
    url = Column(Text, nullable=False)

    # Audit
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    company = relationship(Company)
    system = relationship(AISystem)
    task = relationship(ComplianceTask)
    uploader = relationship(User, foreign_keys=[uploaded_by])

    def __repr__(self) -> str:
        return f"<Document id={self.id} type={self.document_type!r} url={self.url!r} system={self.ai_system_id} task={self.task_id}>"

# Useful composite indexes
Index("ix_documents_system_task", Document.ai_system_id, Document.task_id)
Index("ix_documents_company_type", Document.company_id, Document.document_type)