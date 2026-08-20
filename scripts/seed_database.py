"""Create deterministic, fictional development data and demo users."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.models.customer import Customer
from app.models.enums import PaymentMethod, UserRole
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePurchaseCommand,
    CreateSaleCommand,
    PaymentInput,
    PurchasedPhoneInput,
    SaleLineInput,
)
from app.services.purchase_service import PurchaseService
from app.services.sale_service import SaleService
from app.utils.security import hash_password


def main() -> int:
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("Development seed data is disabled in production.")
    with SessionFactory.begin() as session:
        if session.scalar(select(func.count()).select_from(User)):
            print("Seed skipped: users already exist.")
            return 0
        users = (
            User(
                username="admin",
                full_name="Demo Owner",
                email="owner@example.invalid",
                password_hash=hash_password("admin", allow_weak_demo_password=True),
                role=UserRole.OWNER,
                is_active=True,
                must_change_password=True,
            ),
            User(
                username="manager",
                full_name="Demo Manager",
                email="manager@example.invalid",
                password_hash=hash_password("Manager123!"),
                role=UserRole.MANAGER,
                is_active=True,
                must_change_password=True,
            ),
            User(
                username="sales",
                full_name="Demo Salesperson",
                email="sales@example.invalid",
                password_hash=hash_password("Salesperson123!"),
                role=UserRole.SALESPERSON,
                is_active=True,
                must_change_password=True,
            ),
            User(
                username="inventory",
                full_name="Demo Inventory Manager",
                email="inventory@example.invalid",
                password_hash=hash_password("Inventory123!"),
                role=UserRole.INVENTORY_MANAGER,
                is_active=True,
                must_change_password=True,
            ),
        )
        session.add_all(users)
        session.flush()
        owner = AuthenticatedUser.from_model(users[0])
        supplier = Supplier(
            name="Demo Distribution",
            company_name="Demo Distribution (Pvt.) Ltd.",
            phone="+92 300 0000000",
            email="supplier@example.invalid",
            address="Fictional Trade Centre",
            is_active=True,
            balance=Decimal("0.00"),
        )
        customer = Customer(
            name="Demo Customer",
            phone="+92 311 0000000",
            email="customer@example.invalid",
            address="Fictional Customer Address",
        )
        products = (
            Product(
                brand="Apple",
                model="iPhone 15",
                variant="Standard",
                storage="128GB",
                ram="6GB",
                color="Black",
                category="PHONE",
                default_purchase_price=Decimal("210000.00"),
                default_sale_price=Decimal("230000.00"),
                minimum_stock=2,
                is_active=True,
            ),
            Product(
                brand="Samsung",
                model="Galaxy S24",
                variant="Standard",
                storage="256GB",
                ram="8GB",
                color="Gray",
                category="PHONE",
                default_purchase_price=Decimal("190000.00"),
                default_sale_price=Decimal("215000.00"),
                minimum_stock=2,
                is_active=True,
            ),
            Product(
                brand="Xiaomi",
                model="Redmi Note 13",
                variant="Standard",
                storage="128GB",
                ram="8GB",
                color="Blue",
                category="PHONE",
                default_purchase_price=Decimal("52000.00"),
                default_sale_price=Decimal("59000.00"),
                minimum_stock=3,
                is_active=True,
            ),
        )
        session.add_all((supplier, customer, *products))
        session.flush()
        purchase = PurchaseService(session).create(
            CreatePurchaseCommand(
                supplier_id=supplier.id,
                phones=(
                    PurchasedPhoneInput(
                        product_id=products[0].id,
                        imei_1="356123456789012",
                        imei_2="356123456789013",
                        purchase_price=Decimal("210000.00"),
                        selling_price=Decimal("230000.00"),
                    ),
                    PurchasedPhoneInput(
                        product_id=products[1].id,
                        imei_1="356123456789014",
                        imei_2="356123456789015",
                        purchase_price=Decimal("190000.00"),
                        selling_price=Decimal("215000.00"),
                    ),
                    PurchasedPhoneInput(
                        product_id=products[2].id,
                        imei_1="356123456789016",
                        imei_2="356123456789017",
                        purchase_price=Decimal("52000.00"),
                        selling_price=Decimal("59000.00"),
                    ),
                ),
                payments=(PaymentInput(PaymentMethod.BANK_TRANSFER, Decimal("452000.00")),),
            ),
            owner,
        )
        SaleService(session).create(
            CreateSaleCommand(
                customer_id=customer.id,
                lines=(SaleLineInput("356123456789016"),),
                payments=(PaymentInput(PaymentMethod.CASH, Decimal("59000.00")),),
            ),
            owner,
            owner_email=None,
        )
        print(
            f"Seeded {purchase.purchase_number}, four demo roles, three products, "
            "three serialized phones, and one sale."
        )
        print("DEVELOPMENT ONLY: admin / admin (password change required on first login)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
