# Backup and Restore

Backups use the PostgreSQL `pg_dump` executable and database credentials from the environment.
The client major version must be the same as or newer than the PostgreSQL server major version.
Set `POSTGRES_TOOLS_DIRECTORY` when the matching `pg_dump`, `pg_restore`, and `psql` are not first
on `PATH` (commonly the PostgreSQL `bin` directory on Windows).
Compressed custom-format files use `.dump`; plain SQL uses `.sql`. A backup is first written to
a hidden `.part` file, checked for non-zero size, and atomically renamed. Retention cleanup runs
only after that success, so a failed new backup never deletes an older valid one.

Manual OWNER backup:

```bash
python -m scripts.backup_database
```

Celery beat runs the scheduled backup 30 minutes after the configured daily report. Monitor
worker logs and copy backups to encrypted off-host storage. A local disk copy alone is not a
disaster-recovery strategy.

## Safe restore procedure

1. Restrict access and stop every desktop client, Celery worker, and Celery beat process.
2. Take and preserve a fresh backup of the current database.
3. Verify the selected file belongs to the configured backup directory and its expected date.
4. Restore into a separate test database first and run `alembic current` plus business checks.
5. For custom format:

   ```bash
   pg_restore --host HOST --port PORT --username USER --dbname DATABASE \
     --clean --if-exists --single-transaction backup.dump
   ```

6. For plain SQL:

   ```bash
   psql --host HOST --port PORT --username USER --dbname DATABASE \
     --single-transaction --file backup.sql
   ```

7. Run `alembic upgrade head`, restart services, log in as OWNER, and verify inventory totals,
   the latest invoice, audit history, and email queue.

Only OWNER has in-application restore permission. Never restore while sales terminals are open.
Practice recovery regularly and document the measured recovery time.
