"""create calendar_pins table (company calendar milestones)

Revision ID: 2bb574b06430
Revises: a4621ce65994
Create Date: 2025-09-13 08:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2bb574b06430"
down_revision = "a4621ce65994"   # ← ako ti je zadnja revizija drukčija, stavi njezin ID
branch_labels = None
depends_on = None


def _insp():
    bind = op.get_bind()
    return sa.inspect(bind)


def _has_table(name: str) -> bool:
    try:
        return name in _insp().get_table_names()
    except Exception:
        return False


def _has_index(table: str, idx_name: str) -> bool:
    try:
        idxs = [i.get("name") for i in _insp().get_indexes(table)]
        return idx_name in idxs
    except Exception:
        return False


def upgrade():
    # --- table ---
    if not _has_table("calendar_pins"):
        op.create_table(
            "calendar_pins",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer, nullable=False),
            sa.Column("ai_system_id", sa.Integer, nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("start_at", sa.DateTime, nullable=False),
            sa.Column("end_at", sa.DateTime, nullable=True),
            sa.Column("visibility", sa.String(length=20), nullable=False, server_default="company"),
            sa.Column("severity", sa.String(length=20), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=True, server_default="active"),
            sa.Column("created_by_user_id", sa.Integer, nullable=True),
            sa.Column("updated_by_user_id", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.CheckConstraint("visibility IN ('company','mate_internal')", name="ck_calendar_pins_visibility"),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ai_system_id"], ["ai_systems.id"], ondelete="SET NULL"),
        )

    # --- indexes ---
    if not _has_index("calendar_pins", "ix_calendar_pins_company_time"):
        op.create_index(
            "ix_calendar_pins_company_time",
            "calendar_pins",
            ["company_id", "start_at"],
        )
    if not _has_index("calendar_pins", "ix_calendar_pins_visibility"):
        op.create_index(
            "ix_calendar_pins_visibility",
            "calendar_pins",
            ["visibility"],
        )


def downgrade():
    if _has_index("calendar_pins", "ix_calendar_pins_visibility"):
        op.drop_index("ix_calendar_pins_visibility", table_name="calendar_pins")
    if _has_index("calendar_pins", "ix_calendar_pins_company_time"):
        op.drop_index("ix_calendar_pins_company_time", table_name="calendar_pins")

    if _has_table("calendar_pins"):
        op.drop_table("calendar_pins")