"""Import every mapped class so Alembic sees complete metadata."""

from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.damage import DamageRecord
from app.models.email_history import EmailHistory
from app.models.inventory import InventoryTransaction, PhoneIMEI, PhoneInventory
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.return_record import ReturnItem, SaleReturn
from app.models.sale import Sale, SaleItem
from app.models.settings import AppSetting, DocumentSequence
from app.models.supplier import Supplier
from app.models.user import User

__all__ = [
    "AppSetting",
    "AuditLog",
    "Customer",
    "DamageRecord",
    "DocumentSequence",
    "EmailHistory",
    "InventoryTransaction",
    "Payment",
    "PhoneIMEI",
    "PhoneInventory",
    "Product",
    "Purchase",
    "PurchaseItem",
    "ReturnItem",
    "Sale",
    "SaleItem",
    "SaleReturn",
    "Supplier",
    "User",
]
