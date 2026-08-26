from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dealer import Dealer
from app.models.email_history import EmailHistory
from app.models.enums import InventoryTransactionType, PaymentMethod
from app.models.inventory import StockBatch, StockMovement
from app.models.product import Product
from app.models.supplier import Supplier
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData, ShopProfile
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError


def _purchase_batch(
    session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    number: str = "AGX-GZNR-2603",
) -> StockBatch:
    product.name = "Gazonner 15% EC"
    product.manufacturer = "Avenex Crop Sciences"
    product.formulation = "15% EC"
    product.pack_size = "500-ML"
    product.active_ingredient = "Quizalofop-P-Ethyl"
    product.unit = "PACK"
    StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number=number,
                    quantity=40,
                    cartons=2,
                    packs_per_carton=20,
                    manufacture_date=date(2026, 6, 1),
                    expiry_date=date(2028, 5, 31),
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                ),
            ),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("3000.00")),),
        ),
        owner,
    )
    session.flush()
    return session.scalar(select(StockBatch).where(StockBatch.batch_number == number))  # type: ignore[return-value]


def test_batch_purchase_and_dealer_sale_are_atomic(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _purchase_batch(db_session, owner, supplier, product)
    dealer = DealerService(db_session).create(
        actor=owner,
        name="Hanan",
        business_name="Hanan Spray Center",
        phone="0307-6558192",
        email="hanan@example.invalid",
        address="Baba Market 90/F Tehsil Hasilpur",
        cnic="31203-1726076-1",
        territory="HSP",
        credit_limit=Decimal("10000.00"),
    )
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            dealer_id=dealer.id,
            lines=(PesticideSaleLineInput(batch.id, 5, Decimal("150.00")),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("500.00")),),
            order_number="0421",
            territory="HSP",
            policy="NET SALE",
            store="FINISHED",
        ),
        owner,
        shop_name="Avenex Crop Sciences",
        owner_email="owner@example.invalid",
    )
    db_session.flush()

    assert batch.quantity_available == 35
    assert sale.items[0].quantity == 5
    assert sale.items[0].batch_number == "AGX-GZNR-2603"
    assert sale.total == Decimal("750.00")
    assert dealer.balance == Decimal("250.00")
    assert supplier.balance == Decimal("1000.00")
    movements = list(
        db_session.scalars(
            select(StockMovement)
            .where(StockMovement.batch_id == batch.id)
            .order_by(StockMovement.created_at)
        )
    )
    assert [
        (row.transaction_type, row.quantity_change, row.balance_after) for row in movements
    ] == [
        (InventoryTransactionType.PURCHASE, 40, 40),
        (InventoryTransactionType.SALE, -5, 35),
    ]
    recipients = set(
        db_session.scalars(select(EmailHistory.recipient).where(EmailHistory.entity_id == sale.id))
    )
    assert recipients == {"hanan@example.invalid", "owner@example.invalid"}

    payload = ReceiptGenerator().generate_a4(
        SaleReceiptData.from_sale(sale, "CASH"),
        ShopProfile(name="Avenex Crop Sciences", currency="PKR"),
    )
    assert payload.startswith(b"%PDF")


def test_sale_rejects_quantity_above_available_stock(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _purchase_batch(db_session, owner, supplier, product, number="LIMITED-2603")
    dealer = Dealer(
        name="Dealer",
        phone="0300-1111111",
        credit_limit=Decimal("0.00"),
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(dealer)
    db_session.flush()
    with pytest.raises(ConflictError, match="only 40"):
        PesticideSaleService(db_session).create(
            CreatePesticideSaleCommand(
                dealer_id=dealer.id,
                lines=(PesticideSaleLineInput(batch.id, 41),),
                payments=(),
            ),
            owner,
        )
    assert batch.quantity_available == 40
