# Printing and invoices

A sale produces two documents, each matching the form the shop already uses:

**The A4 delivery challan** (`DELIVERY CHALLAN / INVOICE`) is the goods document that travels with
the delivery. It carries the shop identity, invoice number and date, customer name, address, NIC/tax
number, employee, territory, order number and store, then a line table of `PRODUCT`, `POLICY`,
`BATCH NO.`, `QTY` and `CARTONS - PACKS`. Beneath an open area sit the boxed quantity and carton
totals and the `Prepared By` / `Approved By` / `Dealer's Signature & Stamp` lines.

It deliberately shows **no prices or amounts** — it records what was delivered, not what was
charged, exactly like the pre-printed pad. Amounts belong on the customer's receipt.

**The thermal receipt** is the priced counter document: shop name and address, invoice number,
customer and date, then an `Item Name` / `Qty.` / `Price` / `Sub Total` table, and a summary block
pairing `No of Items` and `Total Qty` on the left with `Total`, `Discount` and `Grand Total` on the
right. When a balance is outstanding it also prints `Paid` and `Balance`.

Set the shop name, owner, address, phone, email, registration/tax details, logo, currency, and
footer under **Shop Settings**. The counter details brand the receipt; the separate **billing**
address, contact number, and email brand the A4 challan and the emailed copy, each falling back to
the counter detail when left blank.

## The shop logo

**Shop Settings > Logo > Choose…** copies the image into the application's own `branding` folder and
stores that copy's path. Referencing the file where it was found means the logo silently disappears
from every document once that file is moved, renamed, or the shop runs on another machine.

The line under the field states whether the logo can actually be printed. If it reads that the image
is missing, choose the file again. A configured logo that cannot be read is also recorded in
`logs/application.log` rather than being skipped in silence.

A thermal head prints pure black and white, so a colour or shaded logo comes out as a smudge. Supply
a high-contrast, solid-black version for receipts to look crisp.

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
  Receipt drivers also advertise the roll as one continuous page -- a POS-80 reports
  `72 x 3276 mm`. The application prints at the receipt's own size and replaces any page the
  driver substitutes, because a 3.3 m page feeds metres of blank paper and shrinks the receipt to
  an unreadable strip. The print preview footer shows the page actually in use, so it should read
  something like `Custom (80 x 109 mm)`, never a height in the hundreds or thousands.
- Check the driver's own "paper width" or "print width" setting matches the roll. A driver set to
  58 mm while an 80 mm roll is loaded prints a narrow, cropped receipt.
- If the printer feeds extra paper after each receipt, that is the driver's cut/feed setting, not
  the receipt layout.
- Print through the Windows driver, not a generic text-only driver: invoices are rendered as a page
  image so they can carry the logo and table rules.
- Raise the driver's **print density** / **darkness** if receipts still look pale. That is a
  hardware setting the application cannot reach, and it is the usual cause of faint output once the
  page geometry is right. Old or low-grade thermal paper also prints grey.

## Print quality on a thermal head

Receipts are rasterised at the printer's own resolution -- one image pixel per printer dot -- and
then reduced to pure black and white. A thermal head can only burn a dot or leave it blank, so any
grey handed to the driver is dithered into scattered dots, which reads as faint, ragged text.
Flattening the page first keeps every stroke solid.

Sheet printers are unaffected: pages wider than 90 mm keep their full greyscale rendering.

PDF export does not require a printer. Customer/dealer and owner emails attach the same generated
invoice through the persistent email outbox.
