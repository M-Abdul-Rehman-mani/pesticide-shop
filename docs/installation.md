# Installation

## Linux development

Install Python 3.11-3.13, Docker Engine with Compose, and PostgreSQL client tools:

```bash
sudo apt update
sudo apt install python3.11-venv postgresql-client libgl1 libegl1 libxkbcommon0 libxcb-cursor0
cp .env.example .env
# Replace every password and APP_SECRET_KEY in .env.
docker compose up -d postgres redis
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,build]'
alembic upgrade head
python -m scripts.create_admin --full-name "Shop Owner" --email owner@example.com
python main.py
```

For disposable demonstration data, development mode alone supports:

```bash
python -m scripts.seed_database
```

This creates `admin` / `admin` with mandatory password change. Never run the seed command in
production.

## Windows development

Install 64-bit Python from python.org, PostgreSQL client tools, and Docker Desktop. In PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,build]"
alembic upgrade head
python -m scripts.create_admin --full-name "Shop Owner" --email owner@example.com
python main.py
```

Add PostgreSQL's matching-version `bin` directory to `PATH`, or set
`POSTGRES_TOOLS_DIRECTORY`, so `pg_dump`, `pg_restore`, and `psql` are available. The client
must not be older than the server.

## Services

Run workers in separate terminals:

```bash
celery -A app.tasks.celery_app worker --loglevel=INFO --queues=email,reports,maintenance
celery -A app.tasks.celery_app beat --loglevel=INFO
```

On Windows, Celery's prefork pool is unavailable; use `--pool=solo`, or run workers in a Linux
container/WSL service. PostgreSQL and Redis may be installed natively instead of Compose; set
the matching host and port in `.env`.

## Configuration

Required production values are `APP_SECRET_KEY`, all `DATABASE_*` credentials, and a reachable
PostgreSQL instance. Redis, SMTP, owner email, report time, backup retention, timezone, and
currency are also environment-configurable. Non-secret shop preferences can then be managed
by an OWNER/MANAGER in Settings. Changing the application key makes saved encrypted SMTP
passwords unreadable, so preserve it in a secret manager.
