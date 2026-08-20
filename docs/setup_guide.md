# Setup Guide

This guide is a single runbook for preparing and running Mobile Shop Manager for the first time.
Use it after cloning the repository.

## 1) Prerequisites

- Python 3.11–3.13
- Docker Engine + Docker Compose
- PostgreSQL client tools (`psql`, `pg_dump`, `pg_restore`) in `PATH` or `POSTGRES_TOOLS_DIRECTORY`
- Redis reachable from application host
- On Linux GUI clients: `libgl1`, `libegl1`, `libxcb-cursor0`, `libxkbcommon0`

## 2) Project bootstrap

```bash
cp .env.example .env
```

Edit `.env` and replace at least:

- `APP_SECRET_KEY`
- `DATABASE_PASSWORD`
- `TEST_DATABASE_PASSWORD`
- `SMTP_*` values (optional during development)

## 3) Start infrastructure

```bash
docker compose up -d postgres redis
```

Optional database checks:

```bash
docker compose ps
docker compose exec postgres pg_isready -U "$DATABASE_USER" -d "$DATABASE_NAME"
```

## 4) Prepare Python environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,build]"
```

On Windows:

```powershell
py -3.11 -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,build]"
```

## 5) Apply migrations

```bash
alembic upgrade head
```

The application enforces PostgreSQL and migration compatibility at startup.

## 6) First owner account

Create the first owner account:

```bash
python -m scripts.create_admin --full-name "Shop Owner" --email owner@example.com
```

Use a strong password at first login, then save it in your approved secret store.

## 7) Start application and background workers

```bash
python main.py
```

Run Celery in separate terminals:

```bash
celery -A app.tasks.celery_app worker --loglevel=INFO --queues=email,reports,maintenance
celery -A app.tasks.celery_app beat --loglevel=INFO
```

## 8) Optional demo data (development only)

```bash
python -m scripts.seed_database
```

This creates the demo account `admin` / `admin` and flags it for mandatory password change.
Use only in non-production environments.

## 9) Daily operations checklist

- Open **Settings → Email** and send a test email.
- Configure owner and shop details.
- Confirm backup directory exists/writable for scheduled dumps.
- Verify report generation and invoice export once after login.
- Confirm first sale flow works with printer/email if enabled.

## 10) Quick troubleshooting

- **Database connection unavailable:** start PostgreSQL, verify `.env`, and rerun `docker compose ps`.
- **Database version mismatch:** run `alembic upgrade head` and restart.
- **Email queue failures:** ensure Redis and Celery are running; retry from email history.
- **Backups failing:** confirm PostgreSQL client path and writable backup directory.
- **No printers:** install/prior configure printers in OS, then restart the app and pick printer in Settings.

