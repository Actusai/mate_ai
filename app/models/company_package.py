# app/models/company_package.py  (preporučeno ime; preimenuj iz company_packeges.py)
from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Numeric,
    ForeignKey,
    Index,
    func,
)
from app.db.base import Base


class CompanyPackage(Base):
    __tablename__ = "company_packages"

    id = Column(Integer, primary_key=True, index=True)

    # Scope
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_id = Column(
        Integer,
        ForeignKey("packages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Canonical billing window (koristi ih ostatak aplikacije)
    starts_at = Column(DateTime, nullable=True)  # UTC
    ends_at = Column(DateTime, nullable=True)  # UTC

    # Status: 'active' | 'cancelled' | 'expired' | 'archived' (može biti NULL na starim zapisima)
    status = Column(String(20), nullable=True, default="active")

    # Snapshot cijena u trenutku ugovaranja/produženja
    billing_term = Column(String(10), nullable=True)  # 'monthly' | 'yearly'
    unit_price_month = Column(Numeric(10, 2), nullable=True)  # snapshot mjesečne cijene
    unit_price_year = Column(Numeric(10, 2), nullable=True)  # snapshot godišnje cijene

    # Audit
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_company_packages_company", "company_id"),)

    # ----------------------------
    # Back-compat helpers (READ/WRITE) – NE stvaraju nove kolone u bazi,
    # samo mapiraju na nova canonical polja.
    # ----------------------------
    @property
    def start_date(self):
        """Backward-compatible alias for starts_at."""
        return self.starts_at

    @start_date.setter
    def start_date(self, value):
        self.starts_at = value

    @property
    def end_date(self):
        """Backward-compatible alias for ends_at."""
        return self.ends_at

    @end_date.setter
    def end_date(self, value):
        self.ends_at = value

    @property
    def is_active(self) -> bool:
        """
        Back-compat boolean zastavica:
        - ako imamo status, 'active' znači True
        - fallback: prema datumu (unutar perioda = True)
        """
        if self.status:
            return (self.status or "").lower() == "active"

        now = datetime.utcnow()
        if self.starts_at and self.ends_at:
            return self.starts_at <= now <= self.ends_at
        if self.ends_at:
            return now <= self.ends_at
        return True  # konservativno

    @is_active.setter
    def is_active(self, value: bool):
        # Mapiramo bool na status (soft mapping; ne mijenja datume)
        self.status = "active" if value else "cancelled"
