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
| Printable width | The strip the print head can actually mark, in mm. Defaults to 72 mm for an 80 mm roll and 48 mm for a 58 mm roll. |

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

## Reprinting from a party's history

The same three actions appear wherever past invoices are listed, so an invoice can be reprinted
without going back to the sales screen:

- **Dealers > Sales History** and **Customers > Purchase History** list the invoices with their
  total, paid, and balance. Select one and use **Save PDF**, **Print Preview**, or **Print Invoice**;
  double-clicking a row opens the preview.
- **Dealers > Account Statement** renders the whole account -- every invoice and payment with a
  running balance -- to **Save PDF** or **Print Preview**, which is the document to hand a dealer
  when they ask what they still owe.

## Paper width versus printable width

A thermal roll is wider than the strip its print head can mark. At the usual 203 dpi a head lays
down 576 dots on an 80 mm roll and 384 on a 58 mm one -- 72 mm and 48 mm -- and the rest of the
paper is a margin the printer physically cannot reach. Content placed there is simply lost, which
shows up as the right-hand column (totals) disappearing.

Receipts are therefore laid out across the printable width and centred on the paper. Rolls vary
between manufacturers, so **Printable width** can be lowered if a particular printer marks a
narrower band.

**Print Alignment Test** prints a calibration strip for the selected printer: a millimetre ruler
measured from the left paper edge, `L` and `R` markers at each end of the configured printable
strip, and a solid `START`/`END` bar spanning it.

- Both `L` and `R` printed, with the full bar: the width is correct.
- `R` or `END` missing, or the bar runs off the edge: lower **Printable width** until they appear.
- Nothing prints at all: the problem is the driver or connection, not the layout.

## Windows notes

- Install the thermal printer's Windows driver and print its own self-test before selecting it in
  the application.
- Set the driver's paper size to the roll, e.g. `80 x 297 mm` or the vendor's `80mm x Receipt`.
  The application reuses a page size the driver already declares when one matches the roll width,
  because receipt drivers substitute their default when handed a custom size, which scales or crops
  the output.
- Check the driver's own "paper width" or "print width" setting matches the roll. A driver set to
  58 mm while an 80 mm roll is loaded prints a narrow, cropped receipt.
- If the printer feeds extra paper after each receipt, that is the driver's cut/feed setting, not
  the receipt layout.
- Print through the Windows driver, not a generic text-only driver: invoices are rendered as a page
  image so they can carry the logo and table rules.

PDF export does not require a printer. Customer/dealer and owner emails attach the same generated
invoice through the persistent email outbox.
