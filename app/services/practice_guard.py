"""Keep practice (TEST) trading and real trading on separate documents.

Reports drop a whole sale or purchase when anything on it is a TEST record. A
document that mixed the two would therefore hide real revenue, while its effect
on a real dealer's balance or real stock stayed in the books. Refusing the mix
keeps each document entirely real or entirely practice.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.utils.exceptions import ValidationError


def _product_flags(session: Session, product_ids: Iterable[uuid.UUID]) -> set[bool]:
    return set(session.scalars(select(Product.is_test).where(Product.id.in_(set(product_ids)))))


def require_unmixed_sale(
    session: Session, product_ids: Iterable[uuid.UUID], *, party_is_test: bool | None
) -> None:
    """``party_is_test`` is None for a walk-in sale, which may be either kind."""

    flags = _product_flags(session, product_ids)
    if flags == {True, False}:
        raise ValidationError("TEST products cannot be sold on the same invoice as real products.")
    products_are_test = flags == {True}
    if party_is_test and not products_are_test:
        raise ValidationError("A TEST customer or dealer can only buy TEST products.")
    if products_are_test and party_is_test is False:
        raise ValidationError(
            "TEST products can only be sold to a TEST customer, a TEST dealer, or a walk-in."
        )


def require_unmixed_purchase(
    session: Session, product_ids: Iterable[uuid.UUID], *, supplier_is_test: bool
) -> None:
    flags = _product_flags(session, product_ids)
    if flags == {True, False}:
        raise ValidationError(
            "TEST products cannot be purchased on the same bill as real products."
        )
    products_are_test = flags == {True}
    if supplier_is_test and not products_are_test:
        raise ValidationError("The TEST supplier can only supply TEST products.")
    if products_are_test and not supplier_is_test:
        raise ValidationError("TEST products can only be purchased from the TEST supplier.")
