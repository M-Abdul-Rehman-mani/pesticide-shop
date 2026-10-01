# Email

SMTP credentials can come from `.env` or the Settings screen. A password saved in PostgreSQL is
encrypted using `APP_SECRET_KEY` and is never displayed again. Use “Send Test Email” before
enabling shop notifications.

Sale and return transactions create `email_history` rows in the same database commit. Right
after the commit the program claims those rows with `FOR UPDATE SKIP LOCKED`, marks an attempt,
builds the current PDF receipt, and sends outside the transaction. Failures become `FAILED` with
a bounded error message. There is no background service: while the program is open it retries
due messages every minute, waiting 2, 4, 8 … minutes (at most an hour) between attempts and
giving up after 6 attempts. The owner can inspect Email History and retry any message by hand.

Required SMTP fields:

```text
SMTP_HOST, SMTP_PORT, SMTP_FROM_EMAIL
SMTP_USERNAME and SMTP_PASSWORD when authentication is required
SMTP_USE_TLS=true for STARTTLS
OWNER_EMAIL for owner notifications and daily reports
```

For Microsoft 365, Gmail, or another provider, use an application password or dedicated SMTP
relay; do not store a personal account password. Confirm the provider's sending limits and SPF,
DKIM, and DMARC configuration. Application logs record delivery failures but redact common
credential assignments.

The daily owner report is sent on request: choose a day under **Settings > Email > Daily owner
report** and press **Send Daily Report**. It goes to the saved owner emails as a PDF containing
sales, gross profit (net of returns), returns, money received by method, top products, low stock,
and outstanding balances.
