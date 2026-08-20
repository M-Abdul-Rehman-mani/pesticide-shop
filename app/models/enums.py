"""Stable persisted enum values shared across the domain."""

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    SALESPERSON = "SALESPERSON"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"


class PhoneStatus(StrEnum):
    IN_STOCK = "IN_STOCK"
    RESERVED = "RESERVED"
    SOLD = "SOLD"
    RETURNED = "RETURNED"
    DAMAGED = "DAMAGED"
    SENT_FOR_REPAIR = "SENT_FOR_REPAIR"
    REPAIRED = "REPAIRED"
    LOST = "LOST"
    CANCELLED = "CANCELLED"


class PhoneCondition(StrEnum):
    NEW = "NEW"
    USED = "USED"
    REFURBISHED = "REFURBISHED"
    OPEN_BOX = "OPEN_BOX"


class InventoryTransactionType(StrEnum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    DAMAGE = "DAMAGE"
    REPAIR = "REPAIR"
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


class ReturnReason(StrEnum):
    CUSTOMER_CHANGED_MIND = "CUSTOMER_CHANGED_MIND"
    DEFECTIVE = "DEFECTIVE"
    WRONG_PRODUCT = "WRONG_PRODUCT"
    WARRANTY = "WARRANTY"
    OTHER = "OTHER"


class ReturnCondition(StrEnum):
    GOOD = "GOOD"
    DAMAGED = "DAMAGED"
    OPENED = "OPENED"
    USED = "USED"


class ReturnStatus(StrEnum):
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class DamageType(StrEnum):
    SCREEN = "SCREEN"
    BODY = "BODY"
    CAMERA = "CAMERA"
    BATTERY = "BATTERY"
    WATER = "WATER"
    SOFTWARE = "SOFTWARE"
    MOTHERBOARD = "MOTHERBOARD"
    OTHER = "OTHER"


class DamageStatus(StrEnum):
    REPORTED = "REPORTED"
    UNDER_REPAIR = "UNDER_REPAIR"
    REPAIRED = "REPAIRED"
    WRITTEN_OFF = "WRITTEN_OFF"
    RESOLVED = "RESOLVED"


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
