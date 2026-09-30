"""The one rule for how healthy a product's stock is, shared by every screen."""

from __future__ import annotations

from enum import StrEnum


class StockLevel(StrEnum):
    OK = "OK"
    LOW = "LOW"
    OUT = "OUT"

    @property
    def label(self) -> str:
        return {"OK": "In stock", "LOW": "Low stock", "OUT": "Out of stock"}[self.value]


def stock_level(in_stock: int, minimum_stock: int) -> StockLevel:
    """Classify a product's total available stock against its reorder level.

    Nothing on hand is always out of stock. "Low" needs a reorder level: a product
    with none set cannot be at or below it, so it is never reported as low.
    """

    if in_stock <= 0:
        return StockLevel.OUT
    if minimum_stock > 0 and in_stock <= minimum_stock:
        return StockLevel.LOW
    return StockLevel.OK


def needs_reorder(in_stock: int, minimum_stock: int) -> bool:
    """At or below a reorder level that has actually been set."""

    return minimum_stock > 0 and in_stock <= minimum_stock
