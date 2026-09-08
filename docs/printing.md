# Printing and invoices

Sales produce an A4 delivery challan/invoice and 58 mm or 80 mm thermal receipts. The A4 layout is
based on the supplied Hanan Spray Center reference and includes recipient/dealer identity, address,
territory, order number, policy, store, product, batch, quantity, cartons/packs, prices, totals,
payment details, and signature areas.

Set the shop name, owner, address, phone, email, registration/tax details, logo, currency, and
footer under **Shop Settings**.

## Choosing the printer and paper

**Settings > Printer** holds two preferences:

| Setting | Effect |
| --- | --- |
| Default printer | The device the print preview lays pages out for and that **Print Invoice** sends to. `System default` follows the operating system. |
| Default receipt format | The paper the **Print Invoice** button renders on: A4, 58 mm thermal, or 80 mm thermal. The default is 80 mm. |

Both are read fresh each time a document is printed, so a change takes effect immediately - no
restart. **Preview Sample Receipt** renders a demonstration invoice on the currently selected
printer and paper so the choice can be checked before saving. A printer that is saved but not
currently connected stays selected and is labelled `(not connected)`.

Thermal pages are sized to their content, so a receipt always prints as one page and no blank roll
is fed after it.

## Printing from the sales screen

Three actions become available once a sale is completed, and on any row of **All Sales**:

- **Save PDF** - writes the A4 invoice to a file. No printer needed.
- **Print Preview** - opens the preview with **Print PDF** and **Save PDF** buttons, a printer
  selector, and zoom controls. Switching the printer re-lays the pages out for that device's paper.
- **Print Invoice** - renders the sale at the configured receipt width and sends it straight to the
  configured printer, with no dialog. This is the counter action for an 80 mm thermal printer.

The printer page size is matched to the generated document with zero margins, so an 80 mm receipt
is not centred on an A4 sheet by the driver.

## Windows notes

- Install the thermal printer's Windows driver and print a test page from Windows before selecting
  it in the application.
- Set the driver's paper size to the roll width (80 mm x receipt) so Windows does not add margins.
- If the printer feeds extra paper, that is the driver's cut/feed setting, not the receipt layout.

PDF export does not require a printer. Customer/dealer and owner emails attach the same generated
invoice through the persistent email outbox.
