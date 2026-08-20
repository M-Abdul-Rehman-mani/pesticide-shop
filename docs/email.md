# Email

SMTP credentials can come from `.env` or the Settings screen. A password saved in PostgreSQL is
encrypted using `APP_SECRET_KEY` and is never displayed again. Use “Send Test Email” before
enabling shop notifications.

Sale and return transactions create `email_history` rows in the same database commit. Celery
claims rows with `FOR UPDATE SKIP LOCKED`, marks an attempt, builds the current PDF receipt,
then sends outside the transaction. Failures become `FAILED` with a bounded error message and
are retried with exponential backoff. The owner can inspect history and retry failed messages.

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

The scheduler queues the daily report at `DAILY_REPORT_TIME` in `APP_TIMEZONE`. The report
contains sales, returns, damage, net sales, exact profit, payment-method totals, top models,
low stock, and outstanding balances.
