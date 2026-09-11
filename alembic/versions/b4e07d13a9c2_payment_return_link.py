"""payment return link

Tie the credit and refund a return raises to the credit note itself, so they can
be told apart from money the shop actually received.

Revision ID: b4e07d13a9c2
Revises: 709539a6c853
Create Date: 2026-09-11 16:20:11.004312
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e07d13a9c2"
down_revision: str | None = "709539a6c853"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("sale_return_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_payments_sale_return_id_sale_returns"),
        "payments",
        "sale_returns",
        ["sale_return_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_payments_sale_return", "payments", ["sale_return_id"], unique=False)
    # Entries already written by a return carry its number as their reference, which
    # is how existing rows are matched to their credit note.
    op.execute(
        """
        UPDATE payments
        SET sale_return_id = sale_returns.id
        FROM sale_returns
        WHERE payments.reference = sale_returns.return_number
          AND payments.sale_id = sale_returns.sale_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_payments_sale_return", table_name="payments")
    op.drop_constraint(
        op.f("fk_payments_sale_return_id_sale_returns"), "payments", type_="foreignkey"
    )
    op.drop_column("payments", "sale_return_id")
