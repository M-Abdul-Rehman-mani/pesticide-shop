from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import PaymentMethod, PhoneStatus, UserRole
from app.models.product import Product
from app.repositories.base import paginate
from app.repositories.customer_repository import CustomerRepository
from app.repositories.inventory_repository import InventoryFilters, InventoryRepository
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import CustomerService, ProductService, SupplierService
from app.services.dto import CreateSaleCommand, PaymentInput, SaleLineInput
from app.services.sale_service import SaleService
from app.utils.exceptions import NotFoundError, PermissionDeniedError, ValidationError


def test_customer_supplier_and_product_lifecycle(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    customer_service = CustomerService(db_session)
    customer = customer_service.create(
        actor=owner,
        name="  Sample Buyer  ",
        phone="+92 300 5550000",
        email="BUYER@TEST.INVALID",
        address="  Demo address ",
    )
    assert customer.name == "Sample Buyer"
    assert customer.email == "buyer@test.invalid"
    customer_service.update(
        customer.id,
        actor=owner,
        name="Updated Buyer",
        phone="+92 300 5550001",
        notes="Frequent customer",
    )
    assert customer.notes == "Frequent customer"

    supplier_service = SupplierService(db_session)
    supplier = supplier_service.create(
        actor=owner,
        name="New Supplier",
        company_name="Example Distribution",
        phone="+92 300 5550010",
        tax_number="TEST-TAX-1",
    )
    supplier_service.update(
        supplier.id,
        actor=owner,
        name="Updated Supplier",
        phone="+92 300 5550011",
        email="supplier2@test.invalid",
    )
    supplier_service.set_active(supplier.id, actor=owner, is_active=False)
    assert not supplier.is_active

    product_service = ProductService(db_session)
    product = product_service.create(
        actor=owner,
        brand="Acme",
        model="Model X",
        storage="256GB",
        color="Blue",
        default_purchase_price=Decimal("90000.00"),
        default_sale_price=Decimal("110000.00"),
        minimum_stock=3,
    )
    product_service.change_prices(
        product.id,
        actor=owner,
        purchase_price=Decimal("91000.00"),
        sale_price=Decimal("112000.00"),
    )
    assert product.default_sale_price == Decimal("112000.00")


def test_catalog_validation_permissions_and_missing_records(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    salesperson = replace(owner, role=UserRole.SALESPERSON)
    with pytest.raises(PermissionDeniedError):
        ProductService(db_session).create(actor=salesperson, brand="A", model="B")
    with pytest.raises(ValidationError, match="name"):
        CustomerService(db_session).create(actor=owner, name=" ", phone="+92 300 5550000")
    with pytest.raises(ValidationError, match="negative"):
        ProductService(db_session).create(actor=owner, brand="A", model="B", minimum_stock=-1)
    missing = uuid.uuid4()
    with pytest.raises(NotFoundError):
        CustomerService(db_session).update(missing, actor=owner, name="A", phone="+92 300 5550000")
    with pytest.raises(NotFoundError):
        SupplierService(db_session).set_active(missing, actor=owner, is_active=False)
    with pytest.raises(NotFoundError):
        ProductService(db_session).change_prices(
            missing,
            actor=owner,
            purchase_price=Decimal("1.00"),
            sale_price=Decimal("2.00"),
        )


def test_paginated_customer_and_inventory_search(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    product: Product,
    purchase_phone: object,
) -> None:
    phone = purchase_phone("350000000000501", "350000000000502")  # type: ignore[operator]
    customers = CustomerRepository(db_session).search("Test Customer", page_size=20)
    assert customers.items == [customer]
    assert customers.total_pages == 1
    assert CustomerRepository(db_session).sales(customer.id) == []
    with pytest.raises(ValueError, match="page"):
        paginate(db_session, db_session.query(Customer).statement, 0, 20)
    with pytest.raises(ValueError, match="page_size"):
        paginate(db_session, db_session.query(Customer).statement, 1, 25)

    repository = InventoryRepository(db_session)
    assert repository.find_by_imei(phone.imei_2 or "") == phone
    assert repository.lookup_details(phone.imei_1).invoice is None  # type: ignore[union-attr]
    page = repository.search(
        InventoryFilters(search=product.model, status=PhoneStatus.IN_STOCK),
        page_size=20,
    )
    assert page.items == [phone]
    low_stock = repository.search(InventoryFilters(low_stock_only=True), page_size=20)
    assert phone in low_stock.items

    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_2 or ""),),
            payments=(PaymentInput(PaymentMethod.CASH, phone.selling_price),),
        ),
        owner,
    )
    db_session.flush()
    details = repository.lookup_details(phone.imei_1)
    assert details is not None
    assert details.invoice == sale.invoice_number
    assert details.customer == customer.name
    assert len(repository.history(phone.id)) == 2
