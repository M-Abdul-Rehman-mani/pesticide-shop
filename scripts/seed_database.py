"""Create deterministic, fictional development data and demo users."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentMethod, UserRole
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
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
        dealer = Dealer(
            name="Hanan",
            business_name="Hanan Spray Center",
            phone="0307-6558192",
            email="dealer@example.invalid",
            address="Baba Market 90/F, Tehsil Hasilpur",
            cnic="31203-1726076-1",
            territory="HSP",
            credit_limit=Decimal("100000.00"),
            balance=Decimal("0.00"),
            is_active=True,
        )
        products = (
            Product(
                manufacturer="Avenex Crop Sciences",
                name="Gazonner",
                active_ingredient="Quizalofop-P-Ethyl",
                formulation="15% EC",
                pack_size="500-ML",
                unit="PACK",
                category="HERBICIDE",
                default_purchase_price=Decimal("920.00"),
                default_sale_price=Decimal("1050.00"),
                minimum_stock=10,
                is_active=True,
            ),
            Product(
                manufacturer="Demo Agro Chemicals",
                name="Crop Shield",
                active_ingredient="Chlorantraniliprole",
                formulation="20% SC",
                pack_size="1-L",
                unit="BOTTLE",
                category="INSECTICIDE",
                default_purchase_price=Decimal("1800.00"),
                default_sale_price=Decimal("2100.00"),
                minimum_stock=8,
                is_active=True,
            ),
            Product(
                manufacturer="Demo Crop Care",
                name="Fungi Stop",
                active_ingredient="Mancozeb",
                formulation="80% WP",
                pack_size="250-G",
                unit="PACK",
                category="FUNGICIDE",
                default_purchase_price=Decimal("540.00"),
                default_sale_price=Decimal("650.00"),
                minimum_stock=12,
                is_active=True,
            ),
        )
        session.add_all((supplier, customer, dealer, *products))
        session.flush()
        purchase = StockPurchaseService(session).create(
            CreateStockPurchaseCommand(
                supplier_id=supplier.id,
                batches=(
                    PurchasedBatchInput(
                        product_id=products[0].id,
                        batch_number="AGX-GZNR-2603",
                        quantity=40,
                        cartons=2,
                        packs_per_carton=20,
                        manufacture_date=date(2026, 3, 1),
                        expiry_date=date(2028, 2, 29),
                        purchase_price=Decimal("920.00"),
                        selling_price=Decimal("1050.00"),
                    ),
                    PurchasedBatchInput(
                        product_id=products[1].id,
                        batch_number="CSC-2607",
                        quantity=24,
                        cartons=2,
                        packs_per_carton=12,
                        manufacture_date=date(2026, 7, 1),
                        expiry_date=date(2028, 6, 30),
                        purchase_price=Decimal("1800.00"),
                        selling_price=Decimal("2100.00"),
                    ),
                    PurchasedBatchInput(
                        product_id=products[2].id,
                        batch_number="FSWP-2605",
                        quantity=60,
                        cartons=3,
                        packs_per_carton=20,
                        manufacture_date=date(2026, 5, 1),
                        expiry_date=date(2028, 4, 30),
                        purchase_price=Decimal("540.00"),
                        selling_price=Decimal("650.00"),
                    ),
                ),
                payments=(PaymentInput(PaymentMethod.BANK_TRANSFER, Decimal("100000.00")),),
            ),
            owner,
        )
        from app.models.inventory import StockBatch

        stock = session.scalar(
            select(StockBatch)
            .where(StockBatch.purchase_id == purchase.id)
            .order_by(StockBatch.created_at)
        )
        assert stock is not None
        PesticideSaleService(session).create(
            CreatePesticideSaleCommand(
                dealer_id=dealer.id,
                lines=(PesticideSaleLineInput(stock.id, 5),),
                payments=(PaymentInput(PaymentMethod.CASH, Decimal("5000.00")),),
                order_number="0421",
                territory="HSP",
                policy="NET SALE",
                store="FINISHED",
            ),
            owner,
            owner_email=None,
        )
        print(
            f"Seeded {purchase.purchase_number}, four demo roles, three pesticide products, "
            "three stock batches, one dealer, one customer, and one sale."
        )
        print("DEVELOPMENT ONLY: admin / admin (password change required on first login)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
