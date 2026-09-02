"""Authorized customer, supplier, and product catalog operations."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission, require_permission
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
        if not has_permission(actor.role, Permission.MANAGE_CUSTOMERS):
            require_permission(actor.role, Permission.CREATE_SALE)
        if not name.strip():
            raise ValidationError("Customer name is required.")
        clean_phone = normalize_phone(phone, required=False)
        clean_cnic = cnic.strip() if cnic else None
        identifiers = []
        if clean_phone:
            identifiers.append(Customer.phone == clean_phone)
        if clean_cnic:
            identifiers.append(Customer.cnic == clean_cnic)
        if identifiers and self._session.scalar(
            select(Customer.id).where(or_(*identifiers)).limit(1)
        ):
            raise ConflictError("A customer with that phone number or CNIC already exists.")
        customer = Customer(
            name=name.strip(),
            phone=clean_phone,
            email=normalize_email(email),
            address=address.strip() if address else None,
            cnic=clean_cnic,
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
        clean_phone = normalize_phone(phone, required=False)
        clean_cnic = cnic.strip() if cnic else None
        identifiers = []
        if clean_phone:
            identifiers.append(Customer.phone == clean_phone)
        if clean_cnic:
            identifiers.append(Customer.cnic == clean_cnic)
        if identifiers and self._session.scalar(
            select(Customer.id).where(Customer.id != customer_id, or_(*identifiers)).limit(1)
        ):
            raise ConflictError("Another customer already uses that phone number or CNIC.")
        customer.name = name.strip()
        customer.phone = clean_phone
        customer.email = normalize_email(email)
        customer.address = address.strip() if address else None
        customer.cnic = clean_cnic
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

    def delete(self, customer_id: uuid.UUID, *, actor: AuthenticatedUser) -> None:
        require_permission(actor.role, Permission.MANAGE_CUSTOMERS)
        customer = self._session.get(Customer, customer_id)
        if customer is None:
            raise NotFoundError("Customer was not found.")
        if self._session.scalar(select(Sale.id).where(Sale.customer_id == customer_id).limit(1)):
            raise ConflictError(
                "This customer has sales history and cannot be deleted. "
                "Keep the record for invoices."
            )
        self._audit.record(
            actor_id=actor.id,
            action="CUSTOMER_DELETED",
            entity_type="Customer",
            entity_id=customer.id,
            old_value={"name": customer.name, "phone": customer.phone},
        )
        self._session.delete(customer)


class DealerService:
    """Create and maintain trade dealer accounts."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def create(
        self,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        business_name: str | None = None,
        email: str | None = None,
        address: str | None = None,
        cnic: str | None = None,
        tax_number: str | None = None,
        territory: str | None = None,
        credit_limit: Decimal = Decimal("0.00"),
        notes: str | None = None,
    ) -> Dealer:
        require_permission(actor.role, Permission.MANAGE_DEALERS)
        if not name.strip():
            raise ValidationError("Dealer name is required.")
        dealer = Dealer(
            name=name.strip(),
            business_name=business_name.strip() if business_name else None,
            phone=normalize_phone(phone),
            email=normalize_email(email),
            address=address.strip() if address else None,
            cnic=cnic.strip() if cnic else None,
            tax_number=tax_number.strip() if tax_number else None,
            territory=territory.strip() if territory else None,
            credit_limit=nonnegative_money(credit_limit, field="Credit limit"),
            balance=Decimal("0.00"),
            notes=notes.strip() if notes else None,
            is_active=True,
        )
        self._session.add(dealer)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("A dealer with that CNIC or tax number already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="DEALER_CREATED",
            entity_type="Dealer",
            entity_id=dealer.id,
            new_value={"name": dealer.name, "business_name": dealer.business_name},
        )
        return dealer

    def set_active(
        self, dealer_id: uuid.UUID, *, actor: AuthenticatedUser, is_active: bool
    ) -> Dealer:
        require_permission(actor.role, Permission.MANAGE_DEALERS)
        dealer = self._session.get(Dealer, dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer was not found.")
        dealer.is_active = is_active
        self._audit.record(
            actor_id=actor.id,
            action="DEALER_ACTIVATED" if is_active else "DEALER_DEACTIVATED",
            entity_type="Dealer",
            entity_id=dealer.id,
            new_value={"is_active": is_active},
        )
        return dealer

    def update(
        self,
        dealer_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        name: str,
        phone: str,
        business_name: str | None = None,
        email: str | None = None,
        address: str | None = None,
        cnic: str | None = None,
        tax_number: str | None = None,
        territory: str | None = None,
        credit_limit: Decimal = Decimal("0.00"),
        notes: str | None = None,
    ) -> Dealer:
        require_permission(actor.role, Permission.MANAGE_DEALERS)
        dealer = self._session.get(Dealer, dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer was not found.")
        if not name.strip():
            raise ValidationError("Dealer name is required.")
        old = {"name": dealer.name, "phone": dealer.phone, "email": dealer.email}
        dealer.name = name.strip()
        dealer.business_name = business_name.strip() if business_name else None
        dealer.phone = normalize_phone(phone)
        dealer.email = normalize_email(email)
        dealer.address = address.strip() if address else None
        dealer.cnic = cnic.strip() if cnic else None
        dealer.tax_number = tax_number.strip() if tax_number else None
        dealer.territory = territory.strip() if territory else None
        dealer.credit_limit = nonnegative_money(credit_limit, field="Credit limit")
        dealer.notes = notes.strip() if notes else None
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("A dealer with that CNIC or tax number already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="DEALER_UPDATED",
            entity_type="Dealer",
            entity_id=dealer.id,
            old_value=old,
            new_value={"name": dealer.name, "phone": dealer.phone, "email": dealer.email},
        )
        return dealer


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
        manufacturer: str,
        name: str,
        category: str = "PESTICIDE",
        active_ingredient: str = "",
        formulation: str = "",
        pack_size: str = "",
        registration_number: str = "",
        unit: str = "PACK",
        description: str | None = None,
        default_purchase_price: Decimal = Decimal("0.00"),
        default_sale_price: Decimal = Decimal("0.00"),
        minimum_stock: int = 0,
    ) -> Product:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not manufacturer.strip() or not name.strip():
            raise ValidationError("Manufacturer and product name are required.")
        if minimum_stock < 0:
            raise ValidationError("Minimum stock cannot be negative.")
        product = Product(
            manufacturer=manufacturer.strip(),
            name=name.strip(),
            active_ingredient=active_ingredient.strip(),
            formulation=formulation.strip(),
            pack_size=pack_size.strip(),
            registration_number=registration_number.strip(),
            unit=unit.strip().upper() or "PACK",
            category=category.strip().upper() or "PESTICIDE",
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

    def update(
        self,
        product_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        manufacturer: str,
        name: str,
        category: str,
        active_ingredient: str,
        formulation: str,
        pack_size: str,
        registration_number: str,
        unit: str,
        description: str | None,
        default_purchase_price: Decimal,
        default_sale_price: Decimal,
        minimum_stock: int,
    ) -> Product:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        product = self._session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product was not found.")
        if not manufacturer.strip() or not name.strip():
            raise ValidationError("Manufacturer and product name are required.")
        if minimum_stock < 0:
            raise ValidationError("Minimum stock cannot be negative.")
        old = {"name": product.display_name, "manufacturer": product.manufacturer}
        product.manufacturer = manufacturer.strip()
        product.name = name.strip()
        product.category = category.strip().upper() or "PESTICIDE"
        product.active_ingredient = active_ingredient.strip()
        product.formulation = formulation.strip()
        product.pack_size = pack_size.strip()
        product.registration_number = registration_number.strip()
        product.unit = unit.strip().upper() or "PACK"
        product.description = description.strip() if description else None
        product.default_purchase_price = nonnegative_money(
            default_purchase_price, field="Default purchase price"
        )
        product.default_sale_price = nonnegative_money(
            default_sale_price, field="Default sale price"
        )
        product.minimum_stock = minimum_stock
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("That product variant already exists.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="PRODUCT_UPDATED",
            entity_type="Product",
            entity_id=product.id,
            old_value=old,
            new_value={"name": product.display_name, "manufacturer": product.manufacturer},
        )
        return product

    def set_active(
        self, product_id: uuid.UUID, *, actor: AuthenticatedUser, is_active: bool
    ) -> Product:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        product = self._session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product was not found.")
        product.is_active = is_active
        self._audit.record(
            actor_id=actor.id,
            action="PRODUCT_ACTIVATED" if is_active else "PRODUCT_DEACTIVATED",
            entity_type="Product",
            entity_id=product.id,
            new_value={"is_active": is_active},
        )
        return product
