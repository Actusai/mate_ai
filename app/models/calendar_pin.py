from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import declarative_base

# Reuse shared Base if postoji; u suprotnom napravi lokalni
try:  # pragma: no cover
    from app.db.base import Base  # type: ignore
except Exception:  # pragma: no cover
    Base = declarative_base()  # type: ignore


class CalendarPin(Base):
    __tablename__ = "calendar_pins"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Scope
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    ai_system_id = Column(
        Integer, ForeignKey("ai_systems.id", ondelete="SET NULL"), nullable=True
    )

    # Content
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Time window
    start_at = Column(DateTime, nullable=False)  # UTC
    end_at = Column(DateTime, nullable=True)  # UTC (optional)

    # Visibility: 'company' (vidi i klijent) | 'mate_internal' (samo Mate tim dodijeljen toj tvrtki)
    visibility = Column(String(20), nullable=False, default="company")

    # Optional metadata
    severity = Column(String(20), nullable=True)  # e.g. info|medium|high
    status = Column(String(32), nullable=True, default="active")

    # Audit
    created_by_user_id = Column(Integer, nullable=True)
    updated_by_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_calendar_pins_company_time", "company_id", "start_at"),
        Index("ix_calendar_pins_visibility", "visibility"),
    )
