"""Import every mapped class so Alembic sees complete metadata."""

from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.email_history import EmailHistory
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.sale import Sale, SaleItem
from app.models.settings import AppSetting, DocumentSequence
from app.models.supplier import Supplier
from app.models.user import User

__all__ = [
    "AppSetting",
    "AuditLog",
    "Customer",
    "Dealer",
    "DocumentSequence",
    "EmailHistory",
    "Payment",
    "Product",
    "Purchase",
    "PurchaseItem",
    "Sale",
    "SaleItem",
    "StockBatch",
    "StockMovement",
    "Supplier",
    "User",
]
