"""Customer search and purchase history queries."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.sale import Sale
from app.repositories.base import Page, paginate


class CustomerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def search(self, query: str, page: int = 1, page_size: int = 50) -> Page[Customer]:
        statement = select(Customer)
        if value := query.strip():
            pattern = f"%{value}%"
            statement = statement.where(
                or_(Customer.name.ilike(pattern), Customer.phone.ilike(pattern))
            )
        return paginate(self._session, statement.order_by(Customer.name), page, page_size)

    def sales(self, customer_id: uuid.UUID) -> list[Sale]:
        return list(
            self._session.scalars(
                select(Sale).where(Sale.customer_id == customer_id).order_by(Sale.sale_date.desc())
            ).all()
        )
