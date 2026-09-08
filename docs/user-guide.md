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

Every row on **Products**, **Customers**, **Dealers**, and **Suppliers** carries a three-dot menu
with **View**, **Edit**, and **Delete**. Deletion is permanent and is therefore refused once a record
has history: a customer or dealer with sales, a supplier with purchases, a product that has been
sold, purchased, or stocked, or a party with an outstanding balance. Deactivate those records
instead — they disappear from the active lists while past invoices stay complete.

## Receiving pesticide inventory

Open **Purchases**, select the supplier and product, then enter the batch number, quantity, cartons,
packs per carton, manufacture date, expiry date, purchase price, and default selling price. Add all
batches for the delivery, enter one or more payment lines, discount, and tax, then complete the
purchase. The purchase, supplier balance, stock batches, payments, audit event, and immutable stock
movements are committed together. Duplicate product/batch combinations and invalid dates are
rejected.

## Creating and printing a sale

Open **Sales** and choose Customer or Dealer. The shortcut button under the name follows that
choice: **+ Add walk-in customer** for a counter sale, **+ Add dealer** to open a full dealer form
without leaving the invoice. A new record is selected for the sale as soon as it is saved.
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

**Edit Batch** corrects a batch's number, manufacture and expiry dates, cartons, packs per carton,
prices, location, and notes. Quantities are deliberately absent from that form: they change only
through Adjust Stock, so the movement ledger stays the single record of every stock change.

**Delete batch** removes a batch from sales and stock figures. It writes the remaining quantity off
through an audited adjustment and deactivates the batch; the batch row itself is kept because
invoices and the movement ledger refer to it. A reason is required.

## Dealer accounts and staged payments

Dealers buy on account and settle in instalments, so a dealer's balance is tracked separately from
any single invoice. The **Dealers** screen shows each dealer's outstanding amount and credit limit,
and a dealer who has paid ahead shows their advance as `PKR 500.00 credit`.

**Record Payment** takes one instalment: amount, method, reference (cheque or transfer number), and
notes. The payment is applied to that dealer's oldest unpaid invoices first, and each invoice's paid
and remaining amounts and payment status update accordingly. If the payment is larger than everything
currently owed, the surplus stays on the account as credit and reduces the next invoice's balance.
The confirmation lists exactly which invoices were settled and by how much.

**Account Statement** is the dealer's full ledger: every invoice as a charge, every payment as a
credit, in date order, with the running balance after each line. The header totals show the amount
invoiced, the amount paid, the outstanding balance, and the credit still available against the
dealer's limit. **Sales History** remains available for an invoice-only view.

Payments are immutable, as they are everywhere in the application. Correct a mistake by recording a
compensating transaction rather than editing history.

## The dashboard

**Dashboard** opens on **All Products**, the whole-shop summary: revenue, profit, units sold, stock
on hand, low-stock products, units expiring within 90 days, and uncollected balances, with charts for
daily sales, gross profit, top sellers, and inventory status.

Every product in the catalogue also gets its own tab. A product tab shows the same period figures
narrowed to that product — units sold, revenue, profit, stock on hand, active batches, units expiring
soon, and stock value at cost — plus its daily sales and profit charts, a reorder note comparing
stock against the minimum level, and a table of its live batches with expiry, quantities, and prices.

The reporting period at the top applies to every tab. Product tabs load when you open them, so a
large catalogue stays quick; press **Refresh** to reload the current view and pick up newly added
products.

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
