# User Guide

## First login

The development seed creates `admin` / `admin` only in development and forces a password change.
Production administrators are created with `python -m scripts.create_admin`. After login, the
sidebar shows only pages permitted for the signed-in role. Inactivity triggers logout.

## Core workflows

### Purchase and inventory intake

Create/select a supplier and product, enter each physical phone with its 15-digit IMEI values,
prices, condition, warranty, and location, then record supplier payment lines. Completing the
purchase atomically creates the purchase, serialized stock, inventory ledger, supplier balance,
payments, and audit entry. Duplicate IMEIs are rejected.

### Sale

Open Sales (`Ctrl+N`), select a customer or walk-in, scan an IMEI, add one or more available
phones, edit each cart item's sale price when needed, set discounts/tax, and enter payment methods.
Only `IN_STOCK` phones can be committed. Save the A4 invoice or open print preview afterward.
Customer and owner receipt emails are queued after commit.

### Return

Enter the original invoice and IMEI, choose reason/condition/refund method, and obtain manager or
owner approval. A refund cannot exceed the original amount paid and the item cannot be returned
twice. Good/opened/used items return to stock with the recorded condition; damaged returns enter
`DAMAGED`. Save or print the return receipt after completion.

### Damage and repair

Record the IMEI, type, description, loss, and repair estimate. The phone enters `DAMAGED` and the
owner notification is queued. Authorized inventory staff can move it through repair and return
it to stock after recording cost and resolution.

## Search and reports

Inventory search accepts IMEI, brand, and model and supports status, supplier, date, low-stock,
and page-size filters. Double-click a phone to see original invoice/customer context and the full
immutable movement history. Reports provide today, yesterday, week, month, or custom ranges with
PDF, professionally formatted Excel, and print output.

## Shortcuts

```text
Ctrl+N  New sale       Ctrl+F  Focus search
Ctrl+P  Print/preview  Ctrl+S  Save current form
Ctrl+R  Refresh        Ctrl+I  Inventory
Ctrl+D  Dashboard      Esc     Close active dialog
```

## Roles

- OWNER: all operations, owner accounts, restore, settings, profit, and audit access.
- MANAGER: operational administration except owner-account management and restore.
- SALESPERSON: sales, customer work, inventory search, and permitted returns.
- INVENTORY_MANAGER: purchasing, stock, suppliers, damage/repair, and inventory reports.

Never share user accounts. Do not correct completed documents by deleting database rows; use the
application's return, void, payment, damage, repair, or inventory-adjustment workflow.
