# Printing

ReportLab generates documents independently of printer communication. The application supports:

- A4 sale invoices and return receipts
- 58 mm thermal sale/return receipts
- 80 mm thermal sale/return receipts
- PDF save, native Qt print dialog, and Qt print preview
- PDF/Excel/print actions for business reports

Select the default printer and receipt width in Settings. The native dialog always enumerates
operating-system printers; no device is hardcoded.

## Windows

Install the manufacturer's printer driver, print a test page in Windows Settings, choose the
correct roll width, disable driver scaling where possible, and then verify preview and a real
receipt. USB thermal printers usually appear as ordinary Windows printers.

## Linux

Install/configure CUPS and the appropriate driver. Verify with `lpstat -p -d`, print a system
test page, then launch the application from the same desktop account. Ensure that account can
access USB devices and the CUPS queue.

Receipts intentionally omit passwords, CNIC values, SMTP data, and internal purchase cost.
Logo, shop name, address, phone, email, tax information, currency, and footer are configurable.
