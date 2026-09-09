"""product barcode and invoice print count

Revision ID: d92f5c1a7e34
Revises: c41d7a9e2b58
Create Date: 2026-09-09 12:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d92f5c1a7e34"
down_revision: str | None = "c41d7a9e2b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("barcode", sa.String(length=64), nullable=True))
    op.create_unique_constraint(op.f("uq_products_barcode"), "products", ["barcode"])
    op.add_column(
        "sales",
        sa.Column("print_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sales", "print_count")
    op.drop_constraint(op.f("uq_products_barcode"), "products", type_="unique")
    op.drop_column("products", "barcode")
