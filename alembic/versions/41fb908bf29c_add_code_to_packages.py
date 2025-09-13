from alembic import op
import sqlalchemy as sa

# Alembic identifiers
revision = "41fb908bf29c"
down_revision = "2bb574b06430"  # ← MORA pokazivati na calendar_pins migraciju
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def _has_index(bind, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        return any(ix.get("name") == index_name for ix in insp.get_indexes(table_name))
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()

    if not _has_column(bind, "packages", "code"):
        op.add_column("packages", sa.Column("code", sa.String(length=50), nullable=True))

    if not _has_index(bind, "packages", "ix_packages_code"):
        op.create_index("ix_packages_code", "packages", ["code"], unique=True)


def downgrade():
    # SQLite-safe via batch_alter_table
    with op.batch_alter_table("packages") as batch_op:
        try:
            batch_op.drop_index("ix_packages_code")
        except Exception:
            pass
        try:
            batch_op.drop_column("code")
        except Exception:
            pass