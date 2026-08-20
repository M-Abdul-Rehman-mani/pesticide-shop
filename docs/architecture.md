# Architecture

Mobile Shop Manager is a native PySide6 desktop client backed exclusively by PostgreSQL.
The UI never contains transaction rules. Widgets create typed commands, background workers
open a short-lived SQLAlchemy session, and application services execute the workflow.

```text
PySide6 UI -> typed command -> service transaction -> SQLAlchemy -> PostgreSQL
                                    |
                                    +-> audit + inventory ledger + email outbox

Celery beat -> Redis -> Celery worker -> SMTP / ReportLab / pg_dump
```

## Boundaries

- `app/ui`: navigation, forms, Qt models, dialogs, background worker coordination.
- `app/services`: permissions, validation, transaction workflows, audit/outbox writes.
- `app/repositories`: indexed lookup and pagination queries.
- `app/models`: normalized SQLAlchemy mappings and persisted enums.
- `app/reports` and `app/printing`: presentation-neutral PDF/Excel data and documents.
- `app/email`: SMTP adapter and reliable database outbox delivery.
- `app/tasks`: Celery entry points for delivery, reports, and backup scheduling.
- `app/database`: PostgreSQL engine, sessions, health checks, and Alembic integration.

The desktop caller owns the transaction through `SessionFactory.begin()`. A sale, return,
purchase, damage, payment, or inventory adjustment either commits completely or rolls back.
SMTP delivery occurs only after commit through `email_history`, so email outages cannot undo
a financial document.

## Concurrency and integrity

Financial workflows lock the exact document or phone row with PostgreSQL `FOR UPDATE OF`.
IMEIs also have a normalized `phone_imeis` projection maintained by database triggers. This
prevents the same value being used as SIM slot 1 on one phone and SIM slot 2 on another,
including concurrent direct database writes. Business ledgers and audit rows are protected by
both ORM listeners and PostgreSQL append-only triggers.

Qt database, PDF, and export work runs in `QThreadPool`; SMTP, recurring reports, and backups
run in Celery. Tables use server-side filtering and pages of 20, 50, or 100 rows.

## Security model

Argon2id hashes passwords. An environment-only `APP_SECRET_KEY` derives the Fernet key used
for database-held SMTP secrets. Permissions are centralized in
`app/security/permissions.py`; hiding a UI page is convenience, while every service repeats
the authorization check. Sessions expire after the configured inactivity period.
