# app/models/task_stats.py
from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    Date,
    DateTime,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship, backref
from app.db.base import Base


class TaskStatsDaily(Base):
    __tablename__ = "task_stats_daily"
    __table_args__ = (
        UniqueConstraint("day", "ai_system_id", name="ux_task_stats_daily"),
    )

    id = Column(Integer, primary_key=True)
    day = Column(Date, nullable=False, index=True)
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ai_system_id = Column(
        Integer,
        ForeignKey("ai_systems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    man_total = Column(Integer, nullable=False, default=0)
    man_done = Column(Integer, nullable=False, default=0)
    open_cnt = Column(Integer, nullable=False, default=0)
    in_progress_cnt = Column(Integer, nullable=False, default=0)
    blocked_cnt = Column(Integer, nullable=False, default=0)
    postponed_cnt = Column(Integer, nullable=False, default=0)
    done_cnt = Column(Integer, nullable=False, default=0)
    overdue_cnt = Column(Integer, nullable=False, default=0)
    due_next_7_cnt = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime, nullable=False, server_default=text("datetime('now')")
    )

    company = relationship(
        "Company", backref=backref("task_stats_daily", lazy="selectin")
    )
    ai_system = relationship(
        "AISystem", backref=backref("task_stats_daily", lazy="selectin")
    )


class OwnerTaskStatsDaily(Base):
    __tablename__ = "owner_task_stats_daily"
    __table_args__ = (
        UniqueConstraint(
            "day", "company_id", "owner_user_id", name="ux_owner_task_stats_daily"
        ),
    )

    id = Column(Integer, primary_key=True)
    day = Column(Date, nullable=False, index=True)
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    total_cnt = Column(Integer, nullable=False, default=0)
    overdue_cnt = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime, nullable=False, server_default=text("datetime('now')")
    )

    company = relationship(
        "Company", backref=backref("owner_task_stats_daily", lazy="selectin")
    )
    owner = relationship(
        "User", backref=backref("owner_task_stats_daily", lazy="selectin")
    )
