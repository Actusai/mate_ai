# app/models/document.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import relationship

# Use the same Base as other models so metadata is unified
from app.models.user import Base
from app.models.company import Company
from app.models.user import User
from app.models.ai_system import AISystem


class Document(Base):
    """
    Technical documentation & evidence for an AI system/company.
    Also used to store generated packs (ZIP) with type='doc_pack_zip'.
    """

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)

    # Ownership / scoping
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
    uploaded_by = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # File/meta
    name = Column(String(255), nullable=False, index=True)  # display name shown in UI
    storage_url = Column(Text, nullable=True)  # path/URL to blob/object storage
    content_type = Column(
        String(120), nullable=True
    )  # e.g., application/pdf, application/zip
    size_bytes = Column(Integer, nullable=True)

    # Annex IV extensions
    type = Column(
        String(50), nullable=True, index=True
    )  # e.g., architecture, datasets, rm_plan, testing, doc_pack_zip
    metadata_json = Column(
        Text, nullable=True
    )  # arbitrary JSON as text (key/value metadata)
    status = Column(
        String(20), nullable=False, default="in_progress", index=True
    )  # 'complete' | 'in_progress' | 'missing'
    review_due_at = Column(
        DateTime, nullable=True, index=True
    )  # when this doc should be reviewed/refreshed

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

    # Relations (lazy joined to avoid N+1 in listings)
    company = relationship(Company, lazy="joined")
    system = relationship(AISystem, lazy="joined")
    uploader = relationship(User, foreign_keys=[uploaded_by], lazy="joined")


# Helpful composite indexes for common queries
Index("ix_documents_company_type", Document.company_id, Document.type)
Index("ix_documents_system_type", Document.ai_system_id, Document.type)
Index("ix_documents_status_due", Document.status, Document.review_due_at)
