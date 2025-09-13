# alembic/versions/<new_rev>_company_packages_unit_prices.py
from alembic import op
import sqlalchemy as sa

revision = "<NEW_REV_ID>"
down_revision = "df32eb598e0f"  # zadnja koja je prošla kod tebe
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("company_packages") as b:
        b.add_column(sa.Column("billing_term", sa.String(10), nullable=True))      # 'monthly' | 'yearly'
        b.add_column(sa.Column("unit_price_month", sa.Numeric(10,2), nullable=True))
        b.add_column(sa.Column("unit_price_year", sa.Numeric(10,2), nullable=True))

def downgrade():
    with op.batch_alter_table("company_packages") as b:
        b.drop_column("unit_price_year")
        b.drop_column("unit_price_month")
        b.drop_column("billing_term")