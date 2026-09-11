# Pesticide Shop Manager user guide

## First-time owner setup

Sign in with the owner account, change the temporary password, then open **Shop Settings**. Add the
shop name, owner name, postal address, contact number, email, website, tax/registration text, and an
optional PNG/JPEG logo. These values brand new invoices, and the shop name and logo replace the placeholder at the top of the sidebar as soon as they are saved. Under **Settings**, configure the invoice and
return prefixes, currency, timezone, SMTP account, owner notification emails, printer/receipt format,
backup, and session timeout. **Owner emails** takes as many addresses as the shop needs: type one and press **Add**, select one
and press **Remove**. Everyone on the list gets a copy of every invoice and the daily report. The
test email goes to the first address.

For Gmail the username is the full address, not the shop name, and the password must be a
16-character App Password generated with 2-Step Verification switched on — Google refuses ordinary
account passwords over SMTP. Use **Send Test Email** before relying on automatic delivery.

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

Scan a product barcode into the **Barcode** box to add it straight to the invoice — most scanners
type the code and press Enter, so one scan is one line. The batch closest to expiry is chosen, which
is the stock that should leave the shelf first. Set a product's barcode under **Products > Edit >
Barcode**; it must be unique across the catalogue.

Open **Sales** and choose Customer or Dealer. The shortcut button under the name follows that
choice: **+ Add walk-in customer** for a counter sale, **+ Add dealer** to open a full dealer form
without leaving the invoice. A new record is selected for the sale as soon as it is saved.
Enter the delivery challan fields (order number, territory, policy, store, delivery address), choose
an available product batch, and enter quantity and unit price. Add multiple batches/products, set
line or invoice discounts, tax, and one or more payments, then complete the sale.

The application locks the selected batches while saving, refuses overselling and dealer credit-limit
violations, deducts the exact quantities, records the movement ledger and payment status, updates the
dealer balance, and preserves the sale.

Two documents come out of a completed sale. **Save PDF** and **Print Preview** produce the A4
delivery challan: shop identity, invoice and date, customer details and NIC/tax identity,
order/territory/store, a product/policy/batch/quantity/cartons table, the boxed quantity totals, and
the prepared/approved/dealer signature lines. It carries no prices — it is the goods document.
**Print Invoice** produces the priced thermal receipt for the customer, with item, quantity, price
and sub total columns and the grand total. See `docs/printing.md`.

Every copy after the first is stamped **DUPLICATE**, so two apparent originals cannot circulate.
Opening the print preview does not count as issuing a copy; printing or saving one does.

The status bar shows a count when invoice emails are waiting to be sent. Sales send their own email
directly, so a growing number here means messages that failed and are waiting to be retried, or a
shop with no SMTP configured yet.

When the recipient has an email address, their PDF is sent automatically, and a separate copy goes
to every address on the owner list. Sending happens as soon as the sale is committed — the invoice is
saved and safe before a byte reaches the mail server — so completing a sale tells you how many
messages went out. Email failure never reverses a sale: a refused login leaves the message recorded
and retryable, and the confirmation says so. Inspect and retry from Email History after fixing SMTP.
If SMTP is not configured at all, messages wait in the queue rather than being marked failed.

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

## Correcting mistakes

Nothing is edited or erased; a correction is always a new, audited entry.

**Edit a sale** — **All Sales > Edit sale** loads the invoice back into the New Sale form. Change
lines, quantities, prices, discounts, or the recipient, then **Save changes**. Only the stock that
actually changed moves, and the invoice keeps its number so the customer's reference still matches.
Payments are not edited here: an invoice with money against it must have that reversed first, and an
invoice with a return against it can no longer be edited. Changing the recipient moves the charge
with it — the previous dealer's account is released in full and the new one takes the whole invoice,
checked against their own credit limit.

**Record a return** — **All Sales > Record a return**, available to OWNER and MANAGER because a
return hands money or credit back, the same as voiding. Enter the quantity coming back on each line;
the dialog totals the credit as you go. Restocked goods return to the batch they came from — clear
the tick for damaged or expired stock that cannot be resold. The credit settles whatever is still
owed on the invoice, and anything beyond that is refunded by the chosen method. The original invoice
is never rewritten; the return is a separate numbered credit document, and a line can never be
returned for more than it was sold.

Every credit note is listed under **Sales > Sale Returns** with its number, date, invoice, recipient,
goods, credit, and whether the money was refunded or set against the balance. Search by return
number, invoice, party, or product. **View details** shows the full note including which lines were
not restocked and why; **Open the invoice** jumps to the sale it came from.

**Void a sale** — the three-dot menu on any row of **All Sales**. Voiding returns the stock to the
batches it came from, removes the charge from the dealer's account, and marks the invoice voided so
it stops counting in reports, dealer statements, and payment allocation. A reason is required, and
only OWNER and MANAGER may do it. An invoice with payments against it must have those reversed
first, so the cash trail stays explicit.

**Reverse a dealer payment** — **Dealers > Reverse a payment**. Choose the entry and give a reason;
an opposite entry is recorded that puts the invoice balance and the dealer's account back where they
were. The original stays visible on the statement, with the reversal beneath it. A payment can only
be reversed once, and a reversal cannot itself be reversed. The credit a return puts against an
invoice is not a payment and cannot be reversed here — that would charge the dealer again for goods
already back on the shelf; sell anything returned in error a second time instead.

## Dealer accounts and staged payments

Dealers buy on account and settle in instalments, so a dealer's balance is tracked separately from
any single invoice. The **Dealers** screen shows each dealer's outstanding amount and credit limit,
and a dealer who has paid ahead shows their advance as `PKR 500 credit`.

**Record Payment** takes one instalment: amount, method, reference (cheque or transfer number), and
notes. The payment is applied to that dealer's oldest unpaid invoices first, and each invoice's paid
and remaining amounts and payment status update accordingly. If the payment is larger than everything
currently owed, the surplus stays on the account as credit and reduces the next invoice's balance.
The confirmation lists exactly which invoices were settled and by how much.

**Account Statement** is the dealer's full ledger: every invoice as a charge, every payment as a
credit, in date order, with the running balance after each line. The header totals show the amount
invoiced, the amount paid, the outstanding balance, and the credit still available against the
dealer's limit. **Save PDF** and **Print Preview** produce the statement as a document to hand or
email to the dealer.

**Sales History** lists that dealer's invoices with their total, paid, and balance, and carries the
same **Save PDF**, **Print Preview**, and **Print Invoice** actions as the sales screen, so any past
invoice can be reprinted from here. Double-clicking a row opens the preview. **Customers >
Purchase History** works the same way.

Payments are immutable, as they are everywhere in the application. Correct a mistake by recording a
compensating transaction rather than editing history.

## The dashboard

The dashboard carries four views: **General**, **Customers**, **Dealers**, and **Products**.

**General** is the whole-shop summary: revenue, profit, units sold, stock on hand, low-stock
products, units expiring within 90 days, and uncollected balances, with charts for daily sales,
gross profit, top sellers, and inventory status.

**Customers** and **Dealers** each show their own side of the trade for the selected period:
revenue, gross profit, invoice count, average sale value, how many bought, how many are on the
books, and what is still outstanding — customer balances from unpaid invoices, dealer balances from
their running accounts. Beneath each is a ranking of buyers by revenue with their invoice and unit
counts, outstanding amount, and last purchase; the ranking's revenue always agrees with the card
above it. Retail and trade are never mixed.

**Products** lists the whole catalogue: product, manufacturer, stock on hand with a reorder note,
active batches, units sold, revenue, profit, last sale, and whether the product is active. Search
narrows the list by product or manufacturer.

Click any product to open its own dashboard — the same period figures narrowed to that product:
units sold, revenue, profit, stock on hand, active batches, units expiring soon, and stock value at
cost, plus its daily sales and profit charts, a reorder note comparing stock against the minimum
level, and a table of its live batches with expiry, quantities, and prices. **← All products**
returns to the list, and **Go to product** at the top opens one straight by name.

The reporting period at the top applies to every view. Each view loads when you open it, so a large
catalogue stays quick; press **Refresh** to reload the current view and pick up newly added
products.

## Previous sales and reports

Open **Reports** and choose a date preset or custom range. **Sales History** lists every prior invoice
line with recipient/type, product, batch, quantity, unit price, discount, total, payment state,
salesperson, and date. Other reports cover profit, inventory value, upcoming expiry, purchases,
payments, dealer balances, and employee sales. Reports can be exported to Excel/PDF or printed.

## Roles and operations

- OWNER: full access, including users, profit, settings, audit, backups, and dealer credit.
- MANAGER: daily operational access except owner-only user and restore operations.
- SALESPERSON: sales, customer/dealer records, dashboard, and inventory viewing. Voiding a sale and
  recording a return are not included; both hand value back and need a manager.
- INVENTORY_MANAGER: products, purchases, suppliers, inventory, adjustments, and reports.

Money is shown and printed in whole rupees throughout — on screen, on the thermal receipt, on the
A4 challan, and in reports. Amounts are still stored and calculated exactly; only the display is
rounded.

Under **Settings > Backup**, **Back Up Now** writes a PostgreSQL dump — this is the file to restore
from. **Back Up To Excel & CSV** writes the same data in a form anyone can open: one spreadsheet
with a sheet per table, and one zip archive with a CSV per table. Both are named for the span of
data they hold, for example `shop-data_from_2026-01-04_to_2026-09-11.xlsx`, so a folder of them
reads at a glance. Password hashes and encrypted settings are never written to these files, and an
existing export of the same span is kept rather than overwritten. Only roles that can manage
settings may run it.

Use the backup command and rehearse restore procedures before deployment. Financial documents,
payments, email history, inventory movements, and audit events are retained; corrections use
explicit payment, adjustment, void, or other controlled workflows rather than deleting history.
