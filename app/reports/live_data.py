"""Filters that keep test records out of every business figure.

Customers, dealers, products, and suppliers can be flagged ``is_test`` so staff can
practise real workflows. Anything touching one of them -- a sale to the test
dealer, a sale containing the test product, the test product's stock, a purchase
from the test supplier, and the payments and returns on those -- is test activity
and never counts toward dashboards, reports, or the owner's daily email.

Test sales are derived from their links rather than stored, so an amended sale
can never be left with a stale flag. Every subquery is uncorrelated
(``correlate(None)``) so it stays self-contained when the outer query selects
from the same table.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, Select, and_, or_, select

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.sale import Sale, SaleItem
from app.models.sale_return import SaleReturn
from app.models.supplier import Supplier


def flagged_product_ids() -> Select[tuple[uuid.UUID]]:
    return select(Product.id).where(Product.is_test.is_(True)).correlate(None)


def flagged_dealer_ids() -> Select[tuple[uuid.UUID]]:
    return select(Dealer.id).where(Dealer.is_test.is_(True)).correlate(None)


def flagged_customer_ids() -> Select[tuple[uuid.UUID]]:
    return select(Customer.id).where(Customer.is_test.is_(True)).correlate(None)


def flagged_sale_ids() -> Select[tuple[uuid.UUID]]:
    """Sales to a test customer or dealer, or containing a test product."""

    return (
        select(Sale.id)
        .where(
            or_(
                Sale.customer_id.in_(flagged_customer_ids()),
                Sale.dealer_id.in_(flagged_dealer_ids()),
                Sale.id.in_(
                    select(SaleItem.sale_id)
                    .where(SaleItem.product_id.in_(flagged_product_ids()))
                    .correlate(None)
                ),
            )
        )
        .correlate(None)
    )


def flagged_purchase_ids() -> Select[tuple[uuid.UUID]]:
    """Purchases from a test supplier, or containing a test product."""

    return (
        select(Purchase.id)
        .where(
            or_(
                Purchase.supplier_id.in_(
                    select(Supplier.id).where(Supplier.is_test.is_(True)).correlate(None)
                ),
                Purchase.id.in_(
                    select(PurchaseItem.purchase_id)
                    .where(PurchaseItem.product_id.in_(flagged_product_ids()))
                    .correlate(None)
                ),
            )
        )
        .correlate(None)
    )


def live_sale() -> ColumnElement[bool]:
    return Sale.id.not_in(flagged_sale_ids())


def live_return() -> ColumnElement[bool]:
    return SaleReturn.sale_id.not_in(flagged_sale_ids())


def live_purchase() -> ColumnElement[bool]:
    return Purchase.id.not_in(flagged_purchase_ids())


def live_batch() -> ColumnElement[bool]:
    return StockBatch.product_id.not_in(flagged_product_ids())


def live_product() -> ColumnElement[bool]:
    return Product.is_test.is_(False)


def live_customer() -> ColumnElement[bool]:
    return Customer.is_test.is_(False)


def live_dealer() -> ColumnElement[bool]:
    return Dealer.is_test.is_(False)


def live_payment() -> ColumnElement[bool]:
    """A payment on nothing test: not a test sale, dealer, purchase, or return."""

    return and_(
        or_(Payment.sale_id.is_(None), Payment.sale_id.not_in(flagged_sale_ids())),
        or_(Payment.dealer_id.is_(None), Payment.dealer_id.not_in(flagged_dealer_ids())),
        or_(Payment.purchase_id.is_(None), Payment.purchase_id.not_in(flagged_purchase_ids())),
        or_(
            Payment.sale_return_id.is_(None),
            Payment.sale_return_id.not_in(
                select(SaleReturn.id)
                .where(SaleReturn.sale_id.in_(flagged_sale_ids()))
                .correlate(None)
            ),
        ),
    )
