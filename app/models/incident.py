# app/models/incidents.py
from __future__ import annotations
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    JSON,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Incident(Base):
    """
    Incident record for an AI system.

    NOTE:
    - Foreign keys are intentionally NOT declared here (to avoid boot-time FK ordering issues).
      We will add proper FK constraints via Alembic once migrations are in place.
    """
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Scoping
    company_id = Column(Integer, nullable=False, index=True)
    ai_system_id = Column(Integer, nullable=False, index=True)
    reported_by = Column(Integer, nullable=True, index=True)

    # When it happened (optional if unknown)
    occurred_at = Column(DateTime, nullable=True, index=True)

    # Classification
    # severity: low | medium | high | critical
    severity = Column(String(20), nullable=True, index=True)
    # type: free-form or taxonomy (e.g. "malfunction", "safety", "security", "data_breach")
    type = Column(String(50), nullable=True, index=True)

    # Content
    summary = Column(String(500), nullable=False)
    details_json = Column(JSON, nullable=True)

    # Workflow
    # status: new | investigating | reported | closed
    status = Column(String(20), nullable=False, server_default="new", index=True)

    # Audit
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return (
            f"<Incident id={self.id} system={self.ai_system_id} company={self.company_id} "
            f"severity={self.severity!r} status={self.status!r} occurred_at={self.occurred_at!r}>"
        )


# Helpful composite indexes (kept outside the class for clarity; created by SQLAlchemy)
Index("ix_incidents_company_status", Incident.company_id, Incident.status)
Index("ix_incidents_system_status", Incident.ai_system_id, Incident.status)
Index("ix_incidents_company_severity", Incident.company_id, Incident.severity)