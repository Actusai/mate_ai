from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "<REV_ID>"            # ⇐ ostavi kako je generirano
down_revision = "41fb908bf29c"   # ⇐ prethodna revizija (add_code_to_packages)
branch_labels = None
depends_on = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade():
    bind = op.get_bind()

    if not _has_column(bind, "packages", "price_month"):
        op.add_column("packages", sa.Column("price_month", sa.Numeric(10, 2), nullable=True))
    if not _has_column(bind, "packages", "price_year"):
        op.add_column("packages", sa.Column("price_year", sa.Numeric(10, 2), nullable=True))

    # (optional) backfill:
    # op.execute("UPDATE packages SET price_month = 0 WHERE price_month IS NULL")
    # op.execute("UPDATE packages SET price_year  = 0 WHERE price_year  IS NULL")


def downgrade():
    # SQLite-safe via batch_alter_table
    with op.batch_alter_table("packages") as batch_op:
        try:
            batch_op.drop_column("price_year")
        except Exception:
            pass
        try:
            batch_op.drop_column("price_month")
        except Exception:
            pass