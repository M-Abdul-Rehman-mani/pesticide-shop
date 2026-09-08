"""dealer account payments

Revision ID: c41d7a9e2b58
Revises: 8b221b346f3b
Create Date: 2026-09-08 20:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c41d7a9e2b58"
down_revision: str | None = "8b221b346f3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("dealer_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_payments_dealer_id_dealers"),
        "payments",
        "dealers",
        ["dealer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payments_dealer_created", "payments", ["dealer_id", "created_at"], unique=False
    )
    # Backfill the dealer behind every payment already recorded against a sale so
    # existing accounts show their full history.
    op.execute(
        """
        UPDATE payments
        SET dealer_id = sales.dealer_id
        FROM sales
        WHERE payments.sale_id = sales.id AND sales.dealer_id IS NOT NULL
        """
    )
    # A dealer may now pay more than they currently owe; the surplus is held as
    # account credit with no document attached.
    op.drop_constraint(
        op.f("ck_payments_exactly_one_document"), "payments", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_payments_exactly_one_document"),
        "payments",
        "num_nonnulls(sale_id, purchase_id) = 1"
        " OR (sale_id IS NULL AND purchase_id IS NULL AND dealer_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM payments WHERE sale_id IS NULL AND purchase_id IS NULL")
    op.drop_constraint(
        op.f("ck_payments_exactly_one_document"), "payments", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_payments_exactly_one_document"),
        "payments",
        "num_nonnulls(sale_id, purchase_id) = 1",
    )
    op.drop_index("ix_payments_dealer_created", table_name="payments")
    op.drop_constraint(op.f("fk_payments_dealer_id_dealers"), "payments", type_="foreignkey")
    op.drop_column("payments", "dealer_id")
