"""login lockout and credit application

Count consecutive failed sign-ins so an account can be locked for a while, and mark
the payment entries that move a dealer's existing account credit onto an invoice
(no money changes hands, so cash figures and statements must leave them out).

Revision ID: f3b8d2a6c1e7
Revises: e6a1c3f9b2d4
Create Date: 2026-10-01 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3b8d2a6c1e7"
down_revision: str | None = "e6a1c3f9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("failed_login_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "payments",
        sa.Column("applied_credit", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("payments", "applied_credit")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
