# app/models/ai_system.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

# Koristimo isti Base kao i ostali modeli
from app.models.user import Base  # Base je već definiran u user.py
from app.models.company import Company
from app.models.user import User

# Dozvoljeni statusi usklađenosti (app-level validacija; DB drži string)
COMPLIANCE_STATUSES = {"unknown", "compliant", "partially_compliant", "non_compliant"}


class AISystem(Base):
    __tablename__ = "ai_systems"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    company = relationship("Company", backref="ai_systems", passive_deletes=True)

    # osnovna meta polja
    name = Column(String(255), nullable=False, index=True)
    purpose = Column(Text, nullable=True)                 # opis/namjena sustava
    lifecycle_stage = Column(String(50), nullable=True)   # npr. "development", "production"
    risk_tier = Column(String(50), nullable=True)         # npr. "prohibited", "high_risk", "limited_risk", "minimal_risk"
    status = Column(String(50), nullable=True)            # npr. "active", "paused", "retired"
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # odgovorna osoba

    # 🔹 NOVO: dinamička usklađenost (ne mijenja postojeće funkcionalnosti)
    #   - compliance_status: "unknown" | "compliant" | "partially_compliant" | "non_compliant"
    #   - compliance_score: 0–100 (opcionalno; npr. % ispunjenih obveza)
    #   - compliance_updated_at: kada je zadnji put ažurirano
    compliance_status = Column(String(30), nullable=False, default="unknown", index=True)
    compliance_score = Column(Integer, nullable=True)      # 0–100, opcionalno
    compliance_updated_at = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # relacije (opcionalno, ali korisno)
    company = relationship(Company, backref="ai_systems")
    owner = relationship(User, foreign_keys=[owner_user_id])

    __table_args__ = (
        # Koristan složeni indeks za listanje/filtriranje po company & compliance
        Index("ix_ai_systems_company_compliance", "company_id", "compliance_status"),
    )

    # Pomoćna property: kombinira risk_tier + compliance_status u efektivni rizik
    @property
    def effective_risk(self) -> str:
        """
        Primjeri:
          - high_risk + compliant           -> controlled_high_risk
          - high_risk + partially_compliant -> elevated_high_risk
          - high_risk + non_compliant       -> critical_risk
          - minimal_risk + non_compliant    -> formal_breach_low_risk
          - ostalo                          -> <risk_tier or unknown>
        """
        rt = (self.risk_tier or "").lower()
        cs = (self.compliance_status or "unknown").lower()

        if rt in {"high_risk", "high-risk", "high"}:
            if cs == "compliant":
                return "controlled_high_risk"
            if cs == "non_compliant":
                return "critical_risk"
            if cs == "partially_compliant":
                return "elevated_high_risk"
            return "high_risk_unknown_compliance"

        if rt in {"limited_risk", "limited-risk", "limited"}:
            if cs == "non_compliant":
                return "elevated_limited_risk"
            return "limited_risk"

        if rt in {"minimal_risk", "minimal-risk", "minimal"}:
            if cs == "non_compliant":
                return "formal_breach_low_risk"
            return "minimal_risk"

        if rt in {"prohibited", "prohibited_risk"}:
            # Prohibited je uvijek kritično po aktu, ali ostavljamo naziv jasan
            return "prohibited"

        return "unknown"