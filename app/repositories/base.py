"""Generic PostgreSQL pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total_items: int

    @property
    def total_pages(self) -> int:
        return max(1, (self.total_items + self.page_size - 1) // self.page_size)


def paginate(session: Session, statement: Select[tuple[T]], page: int, page_size: int) -> Page[T]:
    if page < 1:
        raise ValueError("page must be at least 1")
    if page_size not in {20, 50, 100}:
        raise ValueError("page_size must be one of 20, 50, or 100")
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int(session.scalar(count_statement) or 0)
    items = list(session.scalars(statement.limit(page_size).offset((page - 1) * page_size)).all())
    return Page(items=items, page=page, page_size=page_size, total_items=total)
