"""your message

Revision ID: 159e95693993
Revises: <NEW_REV_ID>
Create Date: 2025-09-13 08:20:34.756907
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "159e95693993"
down_revision = "<NEW_REV_ID>"
branch_labels = None
depends_on = None


# ---------- helpers ----------
def _insp(bind):
    return sa.inspect(bind)

def _is_sqlite(bind) -> bool:
    return bind.dialect.name == "sqlite"

def _has_table(bind, table: str) -> bool:
    try:
        return table in _insp(bind).get_table_names()
    except Exception:
        return False

def _has_column(bind, table: str, col: str) -> bool:
    try:
        return any(c["name"] == col for c in _insp(bind).get_columns(table))
    except Exception:
        return False

def _has_index(bind, table: str, name: str) -> bool:
    try:
        return any(ix.get("name") == name for ix in _insp(bind).get_indexes(table))
    except Exception:
        return False

def _fk_exists(bind, table: str, constrained_cols: list[str], referred_table: str) -> bool:
    try:
        for fk in _insp(bind).get_foreign_keys(table):
            cols = fk.get("constrained_columns") or []
            if sorted(cols) == sorted(constrained_cols) and fk.get("referred_table") == referred_table:
                return True
    except Exception:
        pass
    return False


# ---------- upgrade ----------
def upgrade():
    bind = op.get_bind()
    sqlite = _is_sqlite(bind)

    # Clean up any stale temp table from a previous failed batch on SQLite
    if sqlite:
        try:
            op.execute("DROP TABLE IF EXISTS _alembic_tmp_company_packages")
        except Exception:
            pass

    # --- ai_systems: add AR column + indexes (+ FK on non-SQLite) ---
    if _has_table(bind, "ai_systems"):
        if not _has_column(bind, "ai_systems", "authorized_representative_user_id"):
            op.add_column(
                "ai_systems",
                sa.Column("authorized_representative_user_id", sa.Integer(), nullable=True),
            )

        if not _has_index(bind, "ai_systems", "ix_ai_systems_authorized_representative_user_id"):
            op.create_index(
                "ix_ai_systems_authorized_representative_user_id",
                "ai_systems",
                ["authorized_representative_user_id"],
                unique=False,
            )

        if not _has_index(bind, "ai_systems", "ix_ai_systems_company_ar"):
            op.create_index(
                "ix_ai_systems_company_ar",
                "ai_systems",
                ["company_id", "authorized_representative_user_id"],
                unique=False,
            )

        if not sqlite and not _fk_exists(bind, "ai_systems", ["authorized_representative_user_id"], "users"):
            op.create_foreign_key(
                "fk_ai_systems_ar_user_id_users",
                "ai_systems",
                "users",
                ["authorized_representative_user_id"],
                ["id"],
                ondelete="SET NULL",
            )

    # --- company_packages: add new cols & indexes; avoid defaults on SQLite ---
    if _has_table(bind, "company_packages"):
        if not _has_column(bind, "company_packages", "starts_at"):
            op.add_column("company_packages", sa.Column("starts_at", sa.DateTime(), nullable=True))
        if not _has_column(bind, "company_packages", "ends_at"):
            op.add_column("company_packages", sa.Column("ends_at", sa.DateTime(), nullable=True))
        if not _has_column(bind, "company_packages", "status"):
            op.add_column("company_packages", sa.Column("status", sa.String(length=20), nullable=True))

        # created_at / updated_at
        if not _has_column(bind, "company_packages", "created_at"):
            if sqlite:
                op.add_column("company_packages", sa.Column("created_at", sa.DateTime(), nullable=True))
                op.execute("UPDATE company_packages SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
            else:
                op.add_column(
                    "company_packages",
                    sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
                )
        if not _has_column(bind, "company_packages", "updated_at"):
            if sqlite:
                op.add_column("company_packages", sa.Column("updated_at", sa.DateTime(), nullable=True))
                op.execute("UPDATE company_packages SET updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")
            else:
                op.add_column(
                    "company_packages",
                    sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
                )

        # Helpful indexes (idempotent)
        if not _has_index(bind, "company_packages", "ix_company_packages_company_id"):
            op.create_index("ix_company_packages_company_id", "company_packages", ["company_id"], unique=False)
        if not _has_index(bind, "company_packages", "ix_company_packages_package_id"):
            op.create_index("ix_company_packages_package_id", "company_packages", ["package_id"], unique=False)
        if not _has_index(bind, "company_packages", "ix_company_packages_company"):
            op.create_index("ix_company_packages_company", "company_packages", ["company_id"], unique=False)

        # FKs: add only if missing and not on SQLite
        if not sqlite and not _fk_exists(bind, "company_packages", ["company_id"], "companies"):
            op.create_foreign_key(
                "fk_company_packages_company",
                "company_packages",
                "companies",
                ["company_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if not sqlite and not _fk_exists(bind, "company_packages", ["package_id"], "packages"):
            op.create_foreign_key(
                "fk_company_packages_package",
                "company_packages",
                "packages",
                ["package_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    # --- packages: add created_at; avoid default on SQLite ---
    if _has_table(bind, "packages"):
        if not _has_column(bind, "packages", "created_at"):
            if sqlite:
                op.add_column("packages", sa.Column("created_at", sa.DateTime(), nullable=True))
                op.execute("UPDATE packages SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
            else:
                op.add_column(
                    "packages",
                    sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
                )

        if not _has_index(bind, "packages", "ix_packages_code"):
            try:
                op.create_index("ix_packages_code", "packages", ["code"], unique=True)
            except Exception:
                # If duplicate codes exist in a dev DB, skip index for now.
                pass


# ---------- downgrade ----------
def downgrade():
    bind = op.get_bind()
    sqlite = _is_sqlite(bind)

    # ai_systems
    if _has_table(bind, "ai_systems"):
        if _has_index(bind, "ai_systems", "ix_ai_systems_company_ar"):
            op.drop_index("ix_ai_systems_company_ar", table_name="ai_systems")
        if _has_index(bind, "ai_systems", "ix_ai_systems_authorized_representative_user_id"):
            op.drop_index("ix_ai_systems_authorized_representative_user_id", table_name="ai_systems")
        if not sqlite:
            try:
                op.drop_constraint("fk_ai_systems_ar_user_id_users", "ai_systems", type_="foreignkey")
            except Exception:
                pass
        if _has_column(bind, "ai_systems", "authorized_representative_user_id"):
            op.drop_column("ai_systems", "authorized_representative_user_id")

    # company_packages
    if _has_table(bind, "company_packages"):
        for idx in ("ix_company_packages_company_id", "ix_company_packages_package_id", "ix_company_packages_company"):
            if _has_index(bind, "company_packages", idx):
                op.drop_index(idx, table_name="company_packages")
        if not sqlite:
            for col in ("updated_at", "created_at", "status", "ends_at", "starts_at"):
                if _has_column(bind, "company_packages", col):
                    op.drop_column("company_packages", col)

    # packages
    if _has_table(bind, "packages"):
        if _has_index(bind, "packages", "ix_packages_code"):
            op.drop_index("ix_packages_code", table_name="packages")
        if not sqlite and _has_column(bind, "packages", "created_at"):
            op.drop_column("packages", "created_at")