"""sale returns

Goods coming back from a completed invoice, with the stock they restore.

Revision ID: 709539a6c853
Revises: d92f5c1a7e34
Create Date: 2026-09-11 15:05:25.438397
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "709539a6c853"
down_revision: str | None = "d92f5c1a7e34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sale_returns",
        sa.Column("return_number", sa.String(length=40), nullable=False),
        sa.Column("sale_id", sa.UUID(), nullable=False),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "total", sa.Numeric(precision=16, scale=2), server_default="0.00", nullable=False
        ),
        sa.Column("refunded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("total >= 0", name=op.f("ck_sale_returns_ck_sale_returns_total")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_sale_returns_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sale_id"],
            ["sales.id"],
            name=op.f("fk_sale_returns_sale_id_sales"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_returns")),
        sa.UniqueConstraint("return_number", name=op.f("uq_sale_returns_return_number")),
    )
    op.create_index("ix_sale_returns_date", "sale_returns", ["returned_at"], unique=False)
    op.create_index("ix_sale_returns_sale", "sale_returns", ["sale_id"], unique=False)
    op.create_table(
        "sale_return_items",
        sa.Column("sale_return_id", sa.UUID(), nullable=False),
        sa.Column("sale_item_id", sa.UUID(), nullable=False),
        sa.Column("stock_batch_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "unit_price", sa.Numeric(precision=16, scale=2), server_default="0.00", nullable=False
        ),
        sa.Column(
            "total", sa.Numeric(precision=16, scale=2), server_default="0.00", nullable=False
        ),
        sa.Column("restocked", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_sale_return_items_ck_sale_return_items_quantity")
        ),
        sa.CheckConstraint(
            "total >= 0", name=op.f("ck_sale_return_items_ck_sale_return_items_total")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_sale_return_items_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sale_item_id"],
            ["sale_items.id"],
            name=op.f("fk_sale_return_items_sale_item_id_sale_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sale_return_id"],
            ["sale_returns.id"],
            name=op.f("fk_sale_return_items_sale_return_id_sale_returns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stock_batch_id"],
            ["stock_batches.id"],
            name=op.f("fk_sale_return_items_stock_batch_id_stock_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_return_items")),
    )
    op.create_index(
        "ix_sale_return_items_return", "sale_return_items", ["sale_return_id"], unique=False
    )
    op.create_index(
        "ix_sale_return_items_sale_item", "sale_return_items", ["sale_item_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sale_return_items_sale_item", table_name="sale_return_items")
    op.drop_index("ix_sale_return_items_return", table_name="sale_return_items")
    op.drop_table("sale_return_items")
    op.drop_index("ix_sale_returns_sale", table_name="sale_returns")
    op.drop_index("ix_sale_returns_date", table_name="sale_returns")
    op.drop_table("sale_returns")
