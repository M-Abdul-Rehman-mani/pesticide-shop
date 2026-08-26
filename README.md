# Pesticide Shop Manager

Pesticide Shop Manager is a native PySide6 desktop point-of-sale, batch inventory,
dealer/customer account, invoice, and reporting application for pesticide and crop-care shops.
PostgreSQL is the primary database; Redis and Celery provide reliable asynchronous email,
reporting, and backup work.

The system tracks products by manufacturer, active ingredient, formulation, pack size, batch,
manufacture/expiry date, cartons, packs, and available quantity. Purchases, sales, stock changes,
payments, and dealer balances run in database transactions and preserve immutable audit and stock
movement history. It supports split payments and produces A4 delivery challans/invoices plus
58 mm/80 mm receipts and PDF/Excel reports.

The invoice layout follows the supplied Hanan Spray Center reference: invoice/order number,
recipient, address, NIC/tax identity, territory, policy, store, product, batch number, quantity,
totals, and prepared/approved/recipient signature areas. A completed sale can queue the PDF for
the customer or dealer and a separate copy for the owner.

> **Development credential only:** the seed command creates `admin` / `admin` and marks
> the account for a mandatory password change. Never seed demo credentials in production.

## Quick development setup

Requirements: Python 3.11–3.13, Docker Desktop or Docker Engine with Compose, and the
PostgreSQL client tools (`pg_dump`/`pg_restore`) for backup operations.

```bash
cp .env.example .env
# Change DATABASE_PASSWORD, TEST_DATABASE_PASSWORD, and APP_SECRET_KEY.
docker compose up -d postgres redis
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,build]'
alembic upgrade head
python -m scripts.seed_database
python main.py
```

Start the worker and scheduler in separate terminals:

```bash
celery -A app.tasks.celery_app worker --loglevel=INFO
celery -A app.tasks.celery_app beat --loglevel=INFO
```

The `.env` file is ignored by Git. The application will not silently fall back to
SQLite when PostgreSQL is unavailable.

## Environment variables

| Area | Variables |
|---|---|
| Application | `APP_ENV`, `APP_SECRET_KEY`, `APP_TIMEZONE`, `APP_CURRENCY`, `APP_SESSION_TIMEOUT_MINUTES` |
| PostgreSQL | `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_SSL_MODE` |
| Test PostgreSQL | `TEST_DATABASE_HOST`, `TEST_DATABASE_PORT`, `TEST_DATABASE_NAME`, `TEST_DATABASE_USER`, `TEST_DATABASE_PASSWORD` |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD` |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`, `OWNER_EMAIL` |
| Operations | `DAILY_REPORT_TIME`, `BACKUP_DIRECTORY`, `BACKUP_RETENTION_DAYS`, `BACKUP_COMPRESS`, `LOG_DIRECTORY` |

If PostgreSQL tools are not on `PATH`, set `POSTGRES_TOOLS_DIRECTORY` to a client `bin` directory
whose major version is the same as or newer than the server.

`APP_SECRET_KEY` must remain stable because it encrypts saved SMTP secrets. Use a secret
manager in production and never commit `.env`.

## Running the application

Apply migrations before every upgraded build, then create the first owner and start the UI:

```bash
alembic upgrade head
python -m scripts.create_admin --full-name "Shop Owner" --email owner@example.com
python main.py
```

Startup validates configuration, PostgreSQL connectivity, and the exact Alembic revision before
showing login. If the database is unavailable it stops with a friendly connection message; it
never creates or switches to a local database.

## Main workflow

1. Enter the business name, owner, address, contact, tax/registration, logo, and invoice footer in
   **Shop Settings**. Configure owner and SMTP email under **Settings**.
2. Add pesticide products with active ingredient, formulation, pack size, category, stock unit,
   prices, and low-stock threshold.
3. Add upstream suppliers, retail customers, and trade dealers. Dealer records include business
   name, NIC/tax number, territory, credit limit, balance, address, and email.
4. Receive supplier purchases with batch number, manufacture/expiry dates, quantity, cartons,
   packs per carton, purchase/sale prices, and split payments.
5. Create a sale for a walk-in customer, saved customer, or dealer. Select exact stock batches,
   enter quantities, prices, discounts, policy/order/territory/store details, and payments.
6. Save or preview the PDF invoice. Email delivery to the recipient and owner continues through
   the reliable outbox. Review every previous line sale under **Reports → Sales History**.
7. Use **Inventory** for search, low-stock and expiry filters, movement history, stock value, and
   audited count adjustments.

## Quality gates

```bash
ruff check .
ruff format --check .
mypy app scripts
docker compose --profile test up -d postgres-test
pytest --cov=app --cov-report=term-missing
```

Integration tests refuse to use a database whose name does not end in `_test`. The 80% numeric
gate measures deterministic domain/service code; Qt, Celery wiring, native printer dialogs, and
process entry points are exercised separately by smoke/integration checks and excluded from that
number.

## Database migrations

```bash
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
alembic downgrade -1
```

Review generated migrations before applying them. Never edit production tables by hand.

## Packaging

PyInstaller builds must run on the target operating system; a Windows executable must
therefore be built on Windows and a Linux executable on Linux.

```bash
pyinstaller pesticide_shop.spec --clean --noconfirm
```

The executable still requires reachable PostgreSQL and Redis services. It bundles Python
and application dependencies, not a database server.

Run the build on Windows for `.exe` output and on Linux for a Linux binary—PyInstaller does not
cross-compile. Keep `.env` external to the artifact and apply migrations from a controlled release
bundle before replacing desktop clients.

## Backup and restore

Create a verified manual backup with:

```bash
python -m scripts.backup_database
```

Backups are written to a temporary file and renamed only after `pg_dump` succeeds; retention
cleanup happens afterward. Restore is OWNER-only and must be performed with every client and
worker stopped. See [Backup and restore](docs/backup.md) for the full rehearsal procedure.

## Printing and email

Install printers through the operating system, then select the printer/receipt format in Settings.
SMTP can be configured in `.env` or through the encrypted Settings screen. Failed messages remain
in Email History with attempt counts and can be retried without reversing the sale.

## Operations and documentation

- [Architecture](docs/architecture.md)
- [Database](docs/database.md)
- [Setup guide](docs/setup_guide.md)
- [Installation](docs/installation.md)
- [Deployment](docs/deployment.md)
- [Email](docs/email.md)
- [Printing](docs/printing.md)
- [Backup and restore](docs/backup.md)
- [User guide](docs/user-guide.md)

Application logs rotate under `logs/`. Generated exports are written only when selected
by a user. Business documents, payments, inventory ledger entries, and audit events are
append-only; corrections use explicit void or audited stock-adjustment workflows.

## Troubleshooting

- **Database connection unavailable:** verify the PostgreSQL health check, credentials, port, and
  firewall; run `docker compose ps` and `pg_isready`.
- **Database upgrade required:** activate the same release environment and run
  `alembic upgrade head`.
- **Email remains FAILED:** verify Redis/Celery are running, send a Settings test email, and inspect
  `logs/email.log` without copying credentials into support messages.
- **No printers appear:** install/test the printer in Windows or CUPS first, then restart the app.
- **Backup fails:** ensure PostgreSQL client tools are on `PATH` and the configured directory is
  writable. Existing successful backups are retained after a failure.
