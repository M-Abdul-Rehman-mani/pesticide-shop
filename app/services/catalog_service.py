"""Authorized customer, supplier, and product catalog operations."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money, normalize_email, normalize_phone


class CustomerService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def create(
        self,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        cnic: str | None = None,
        notes: str | None = None,
    ) -> Customer:
        require_permission(actor.role, Permission.MANAGE_CUSTOMERS)
        if not name.strip():
            raise ValidationError("Customer name is required.")
        customer = Customer(
            name=name.strip(),
            phone=normalize_phone(phone),
            email=normalize_email(email),
            address=address.strip() if address else None,
            cnic=cnic.strip() if cnic else None,
            notes=notes.strip() if notes else None,
        )
        self._session.add(customer)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("A customer with that CNIC already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="CUSTOMER_CREATED",
            entity_type="Customer",
            entity_id=customer.id,
            new_value={"name": customer.name, "phone": customer.phone},
        )
        return customer

    def update(
        self,
        customer_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        email: str | None = None,
        address: str | None = None,
        cnic: str | None = None,
        notes: str | None = None,
    ) -> Customer:
        require_permission(actor.role, Permission.MANAGE_CUSTOMERS)
        customer = self._session.get(Customer, customer_id)
        if customer is None:
            raise NotFoundError("Customer was not found.")
        old = {"name": customer.name, "phone": customer.phone, "email": customer.email}
        if not name.strip():
            raise ValidationError("Customer name is required.")
        customer.name = name.strip()
        customer.phone = normalize_phone(phone)
        customer.email = normalize_email(email)
        customer.address = address.strip() if address else None
        customer.cnic = cnic.strip() if cnic else None
        customer.notes = notes.strip() if notes else None
        self._audit.record(
            actor_id=actor.id,
            action="CUSTOMER_UPDATED",
            entity_type="Customer",
            entity_id=customer.id,
            old_value=old,
            new_value={"name": customer.name, "phone": customer.phone, "email": customer.email},
        )
        return customer


class SupplierService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def create(
        self,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        company_name: str | None = None,
        email: str | None = None,
        address: str | None = None,
        tax_number: str | None = None,
        notes: str | None = None,
    ) -> Supplier:
        require_permission(actor.role, Permission.MANAGE_SUPPLIERS)
        if not name.strip():
            raise ValidationError("Supplier name is required.")
        supplier = Supplier(
            name=name.strip(),
            company_name=company_name.strip() if company_name else None,
            phone=normalize_phone(phone),
            email=normalize_email(email),
            address=address.strip() if address else None,
            tax_number=tax_number.strip() if tax_number else None,
            notes=notes.strip() if notes else None,
            is_active=True,
            balance=Decimal("0.00"),
        )
        self._session.add(supplier)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("A supplier with that tax number already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="SUPPLIER_CREATED",
            entity_type="Supplier",
            entity_id=supplier.id,
            new_value={"name": supplier.name, "phone": supplier.phone},
        )
        return supplier

    def update(
        self,
        supplier_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        company_name: str | None = None,
        email: str | None = None,
        address: str | None = None,
        tax_number: str | None = None,
        notes: str | None = None,
    ) -> Supplier:
        require_permission(actor.role, Permission.MANAGE_SUPPLIERS)
        supplier = self._session.get(Supplier, supplier_id)
        if supplier is None:
            raise NotFoundError("Supplier was not found.")
        if not name.strip():
            raise ValidationError("Supplier name is required.")
        old = {"name": supplier.name, "phone": supplier.phone, "email": supplier.email}
        supplier.name = name.strip()
        supplier.phone = normalize_phone(phone)
        supplier.company_name = company_name.strip() if company_name else None
        supplier.email = normalize_email(email)
        supplier.address = address.strip() if address else None
        supplier.tax_number = tax_number.strip() if tax_number else None
        supplier.notes = notes.strip() if notes else None
        self._audit.record(
            actor_id=actor.id,
            action="SUPPLIER_UPDATED",
            entity_type="Supplier",
            entity_id=supplier.id,
            old_value=old,
            new_value={"name": supplier.name, "phone": supplier.phone, "email": supplier.email},
        )
        return supplier

    def set_active(
        self, supplier_id: uuid.UUID, *, actor: AuthenticatedUser, is_active: bool
    ) -> Supplier:
        require_permission(actor.role, Permission.MANAGE_SUPPLIERS)
        supplier = self._session.get(Supplier, supplier_id)
        if supplier is None:
            raise NotFoundError("Supplier was not found.")
        old = supplier.is_active
        supplier.is_active = is_active
        self._audit.record(
            actor_id=actor.id,
            action="SUPPLIER_ACTIVATED" if is_active else "SUPPLIER_DEACTIVATED",
            entity_type="Supplier",
            entity_id=supplier.id,
            old_value={"is_active": old},
            new_value={"is_active": is_active},
        )
        return supplier


class ProductService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def create(
        self,
        *,
        actor: AuthenticatedUser,
        brand: str,
        model: str,
        variant: str = "",
        storage: str = "",
        ram: str = "",
        color: str = "",
        category: str = "PHONE",
        description: str | None = None,
        default_purchase_price: Decimal = Decimal("0.00"),
        default_sale_price: Decimal = Decimal("0.00"),
        minimum_stock: int = 0,
    ) -> Product:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not brand.strip() or not model.strip():
            raise ValidationError("Brand and model are required.")
        if minimum_stock < 0:
            raise ValidationError("Minimum stock cannot be negative.")
        product = Product(
            brand=brand.strip(),
            model=model.strip(),
            variant=variant.strip(),
            storage=storage.strip(),
            ram=ram.strip(),
            color=color.strip(),
            category=category.strip().upper() or "PHONE",
            description=description.strip() if description else None,
            default_purchase_price=nonnegative_money(
                default_purchase_price, field="Default purchase price"
            ),
            default_sale_price=nonnegative_money(default_sale_price, field="Default sale price"),
            minimum_stock=minimum_stock,
            is_active=True,
        )
        self._session.add(product)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("That product variant already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="PRODUCT_CREATED",
            entity_type="Product",
            entity_id=product.id,
            new_value={"name": product.display_name},
        )
        return product

    def change_prices(
        self,
        product_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        purchase_price: Decimal,
        sale_price: Decimal,
    ) -> Product:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        product = self._session.execute(
            select(Product).where(Product.id == product_id).with_for_update(of=Product)
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product was not found.")
        old = {
            "default_purchase_price": str(product.default_purchase_price),
            "default_sale_price": str(product.default_sale_price),
        }
        product.default_purchase_price = nonnegative_money(
            purchase_price, field="Default purchase price"
        )
        product.default_sale_price = nonnegative_money(sale_price, field="Default sale price")
        self._audit.record(
            actor_id=actor.id,
            action="PRICE_CHANGED",
            entity_type="Product",
            entity_id=product.id,
            old_value=old,
            new_value={
                "default_purchase_price": str(product.default_purchase_price),
                "default_sale_price": str(product.default_sale_price),
            },
        )
        return product
