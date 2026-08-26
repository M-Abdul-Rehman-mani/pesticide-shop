# Setup guide

1. Copy `.env.example` to `.env` and replace the development secrets.
2. Run `docker compose --profile test up -d postgres postgres-test redis`.
3. Create the Python environment with `uv sync --all-extras` or install `.[dev,build]` in a venv.
4. Run `alembic upgrade head`.
5. Create an owner with `python -m scripts.create_admin`, or use
   `python -m scripts.seed_database` for fictional development data.
6. Start the desktop application with `python main.py`.

Configure business identity in **Shop Settings** and SMTP/owner email in **Settings** before live
use. Run a test email, invoice preview, database backup, and restore rehearsal before deployment.
