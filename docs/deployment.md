# Deployment and Packaging

## PyInstaller

Build on the operating system you will distribute to:

```bash
python -m pip install -e '.[build]'
pyinstaller pesticide_shop.spec --clean --noconfirm
```

The result is `dist/PesticideShopManager` on Linux or `dist/PesticideShopManager.exe` on Windows.
PyInstaller bundles Python, PySide6, migrations, and application libraries. It does not bundle
PostgreSQL, Redis, printer drivers, or PostgreSQL client utilities.

Place a production `.env` beside the executable's working directory, restrict its filesystem
permissions, run `alembic upgrade head` from the release source/administration bundle, and
start Celery worker/beat as supervised services. Do not package `.env` into the executable.

## Linux production

- Use a dedicated OS account and directory such as `/opt/pesticide-shop`.
- Protect `.env` with mode `0600`; give the account write access only to `logs`, `backups`, and
  the operator-selected export directory.
- Run PostgreSQL/Redis with persistent volumes and host firewall rules.
- Manage Celery worker/beat with systemd and restart-on-failure.
- Launch the desktop process from the user's graphical session.

## Windows production

- Install the matching Visual C++ runtime, PostgreSQL client tools, and printer drivers.
- Store configuration in a restricted application directory, not inside `Program Files` if the
  process must write logs.
- Run PostgreSQL/Redis remotely, through Docker Desktop, or as managed services.
- Use Task Scheduler or a service wrapper for Celery. Use `--pool=solo` for a native Windows
  worker.
- Build and code-sign the `.exe` on Windows. Test Windows Defender reputation and the installer
  before shop rollout.

## Release checklist

1. Back up the database and verify the backup is non-empty.
2. Run `ruff check .`, `ruff format --check .`, `mypy app scripts`, and the full pytest command.
3. Run `alembic check` and apply `alembic upgrade head` in staging.
4. Exercise login, forced password change, sale, return, receipt preview, and email retry.
5. Build on each target OS and smoke-run the artifact against staging PostgreSQL/Redis.
6. Deploy migrations before the desktop executable and retain the prior signed artifact.
