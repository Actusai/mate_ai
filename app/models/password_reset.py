from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from datetime import datetime, timedelta
from app.db.base import Base

class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, default="pending")  # pending | used | expired
    # default: 30 min; možeš promijeniti na 60*60 za 1h, itd.
    expires_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(minutes=30))
    created_at = Column(DateTime, default=datetime.utcnow)
