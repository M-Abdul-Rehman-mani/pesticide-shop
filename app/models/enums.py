"""Stable persisted enum values shared across the domain."""

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    SALESPERSON = "SALESPERSON"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"


class InventoryTransactionType(StrEnum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    ADJUSTMENT = "ADJUSTMENT"
    TRANSFER = "TRANSFER"


class PaymentMethod(StrEnum):
    CASH = "CASH"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    MOBILE_WALLET = "MOBILE_WALLET"
    OTHER = "OTHER"


class PaymentStatus(StrEnum):
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    UNPAID = "UNPAID"
    REFUNDED = "REFUNDED"


class PaymentDirection(StrEnum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


class PurchaseStatus(StrEnum):
    DRAFT = "DRAFT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SaleStatus(StrEnum):
    COMPLETED = "COMPLETED"
    VOIDED = "VOIDED"


class EmailStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class SettingCategory(StrEnum):
    GENERAL = "GENERAL"
    SHOP = "SHOP"
    EMAIL = "EMAIL"
    PRINTER = "PRINTER"
    RECEIPT = "RECEIPT"
    BACKUP = "BACKUP"
    DATABASE = "DATABASE"
    SECURITY = "SECURITY"
