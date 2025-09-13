# app/models/package.py
from sqlalchemy import Column, Integer, String, Float, Numeric, DateTime, func
from app.db.base import Base


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)

    # NEW: stable package code (seed koristi ovo)
    # Ostavljeno nullable=True radi kompatibilnosti s postojećom bazom;
    # u migracijama je dodan stupac, potencijalno bez NOT NULL.
    code = Column(String(50), unique=True, index=True, nullable=True)

    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)

    # LEGACY: stari single-price (ostavljen radi kompatibilnosti koda)
    price = Column(Float, default=0.0)

    # NEW: zasebne cijene za MRR/ARR
    price_month = Column(Numeric(10, 2), nullable=True)
    price_year = Column(Numeric(10, 2), nullable=True)

    # Limits / flags
    ai_system_limit = Column(Integer, default=0)  # AR = 0
    user_limit = Column(Integer, default=1)  # broj korisnika u AR timu
    client_limit = Column(Integer, default=0)  # broj klijenata koje AR smije zastupati
    is_ar_only = Column(Integer, default=0)  # 0/1 kao boolean

    # Audit
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    # --- helpers (ne utječu na DB) ---
    @property
    def mrr(self) -> float:
        """Monthly price as float (fallback na legacy 'price' ako month nije postavljen)."""
        try:
            return (
                float(self.price_month)
                if self.price_month is not None
                else float(self.price or 0.0)
            )
        except Exception:
            return float(self.price or 0.0)

    @property
    def arr(self) -> float:
        """Yearly price as float (preferira price_year; fallback 12 * MRR)."""
        try:
            if self.price_year is not None:
                return float(self.price_year)
            return 12.0 * self.mrr
        except Exception:
            return 12.0 * self.mrr
