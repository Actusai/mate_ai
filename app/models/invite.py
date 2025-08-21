from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from datetime import datetime, timedelta
from app.db.base import Base

class Invite(Base):
    __tablename__ = "invites"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    role = Column(String, default="member")  # admin / member / readonly
    status = Column(String, default="pending")  # pending / accepted / expired
    expires_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=7))

