# app/models/system_assignment.py
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base  # ⟵ OVO je bitno!

class SystemAssignment(Base):
    __tablename__ = "system_assignments"

    id = Column(Integer, primary_key=True, index=True)
    ai_system_id = Column(Integer, ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # npr. "viewer" | "contributor" | "owner"
    role = Column(String(32), nullable=False, default="contributor")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # relacije (opcionalno, ali korisno)
    ai_system = relationship("AISystem", back_populates="assignments")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("ai_system_id", "user_id", name="uq_assignment_system_user"),
    )

    ai_system = relationship("AISystem", backref="assignments")
    user = relationship("User", backref="system_assignments")