# app/models/notification.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    ai_system_id = Column(
        Integer, ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True
    )
    task_id = Column(
        Integer, ForeignKey("compliance_tasks.id", ondelete="CASCADE"), nullable=True
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    channel = Column(
        String(30), nullable=False, default="log"
    )  # "log" za sada; kasnije "email"/"slack"
    subject = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)

    status = Column(String(20), nullable=False, default="queued")  # queued|sent|failed
    error_text = Column(Text, nullable=True)

    scheduled_for = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
