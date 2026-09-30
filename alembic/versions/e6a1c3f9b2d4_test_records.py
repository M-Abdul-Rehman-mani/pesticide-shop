"""test records

Flag practice customers, dealers, products, and suppliers so staff can try real
workflows without the results reaching any dashboard, report, or daily email.

Revision ID: e6a1c3f9b2d4
Revises: b4e07d13a9c2
Create Date: 2026-09-30 21:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6a1c3f9b2d4"
down_revision: str | None = "b4e07d13a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("customers", "dealers", "products", "suppliers")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("is_test", sa.Boolean(), server_default="false", nullable=False),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "is_test")
