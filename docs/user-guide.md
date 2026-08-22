# Pesticide Shop Manager user guide

## First-time owner setup

Sign in with the owner account, change the temporary password, then open **Shop Settings**. Add the
shop name, owner name, postal address, contact number, email, website, tax/registration text, and an
optional PNG/JPEG logo. These values brand new invoices. Under **Settings**, configure the invoice
prefix, currency, timezone, SMTP account, owner notification email, printer/receipt format, backup,
and session timeout. Use **Send Test Email** before relying on automatic delivery.

## Products and parties

Open **Products** to add each pesticide, herbicide, insecticide, fungicide, fertilizer, or other
crop-care product. Record product name, manufacturer, active ingredient, formulation, pack size,
registration number, stock unit, purchase/sale prices, and the low-stock threshold.

Use **Suppliers** for companies from which the shop buys stock. Use **Customers** for normal retail
buyers. Use **Dealers** for trade buyers; dealer records also carry business/shop name, NIC/CNIC,
tax number, territory, credit limit, outstanding balance, delivery address, and email. Each customer
and dealer screen includes its own invoice history.

## Receiving pesticide inventory

Open **Purchases**, select the supplier and product, then enter the batch number, quantity, cartons,
packs per carton, manufacture date, expiry date, purchase price, and default selling price. Add all
batches for the delivery, enter one or more payment lines, discount, and tax, then complete the
purchase. The purchase, supplier balance, stock batches, payments, audit event, and immutable stock
movements are committed together. Duplicate product/batch combinations and invalid dates are
rejected.

## Creating and printing a sale

Open **Sales** and choose Customer or Dealer. A customer may be walk-in; a dealer must be saved.
Enter the delivery challan fields (order number, territory, policy, store, delivery address), choose
an available product batch, and enter quantity and unit price. Add multiple batches/products, set
line or invoice discounts, tax, and one or more payments, then complete the sale.

The application locks the selected batches while saving, refuses overselling and dealer credit-limit
violations, deducts the exact quantities, records the movement ledger and payment status, updates the
dealer balance, and preserves the sale. Use **Save PDF** or **Print Preview** after completion. The A4
delivery challan/invoice contains the shop identity, invoice/date, customer/dealer details, NIC/tax
identity, order/territory/store/policy, product/batch/quantity/price lines, totals, payment balance,
and prepared/approved/recipient signature areas.

When the recipient has an email address, their PDF is queued automatically. If the owner email is
configured, a separate owner copy is queued. Email failure never reverses a sale; inspect and retry
it from Email History after fixing SMTP/worker connectivity.

## Inventory, expiry, and corrections

**Inventory** searches by product, manufacturer, active ingredient, batch, or supplier. Filter for
in-stock, out-of-stock, low-stock, expired, or expiring-within-90-days inventory. Every row shows
available/received quantities, unit, expiry status, supplier, prices, and current stock value.
**Batch History** shows every purchase, sale, and adjustment with its resulting balance.

Use **Adjust Stock** only after a physical count, spill, expiry disposal, or other correction. Enter
the new available quantity and a mandatory reason. The application adds an immutable movement and
audit event instead of silently rewriting history.

## Previous sales and reports

Open **Reports** and choose a date preset or custom range. **Sales History** lists every prior invoice
line with recipient/type, product, batch, quantity, unit price, discount, total, payment state,
salesperson, and date. Other reports cover profit, inventory value, upcoming expiry, purchases,
payments, dealer balances, and employee sales. Reports can be exported to Excel/PDF or printed.

## Roles and operations

- OWNER: full access, including users, profit, settings, audit, backups, and dealer credit.
- MANAGER: daily operational access except owner-only user and restore operations.
- SALESPERSON: sales, customer/dealer records, dashboard, and inventory viewing.
- INVENTORY_MANAGER: products, purchases, suppliers, inventory, adjustments, and reports.

Use the backup command and rehearse restore procedures before deployment. Financial documents,
payments, email history, inventory movements, and audit events are retained; corrections use
explicit payment, adjustment, void, or other controlled workflows rather than deleting history.
