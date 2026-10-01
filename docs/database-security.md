# Database Security

Every desktop client connects straight to PostgreSQL with the credentials in its
`.env`. The application checks roles (Owner, Manager, Salesperson, Inventory Manager)
in Python before each operation. Anyone who can read `.env` on a shop computer can
connect with `psql` directly, though, and the database only enforces what that
**database login** is allowed to do.

The ledgers are also append-only only inside the application. Payments, stock
movements and audit entries cannot be changed through the app, but a login with
`UPDATE`/`DELETE` rights on those tables can alter them directly.

So the login in `.env` should be able to do exactly what the app needs and no more.
Never use `postgres` or any other superuser there.

## 1. Create a least-privilege application login

Run once as the database owner/superuser (for example
`psql -U postgres -d pest_shop`), choosing a long random password:

```sql
CREATE ROLE pesticide_app LOGIN PASSWORD 'replace-with-a-long-random-password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT CONNECT ON DATABASE pest_shop TO pesticide_app;
GRANT USAGE ON SCHEMA public TO pesticide_app;

-- Ordinary read/write access to the business tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pesticide_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pesticide_app;

-- Append-only ledgers: the app only ever adds rows to these.
REVOKE UPDATE, DELETE, TRUNCATE ON payments, stock_movements, audit_logs FROM pesticide_app;

-- Schema history is changed by migrations only.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM pesticide_app;
```

Then point every client's `.env` at it:

```
DATABASE_USER=pesticide_app
DATABASE_PASSWORD=<the password above>
```

Keep the owner/superuser password off the shop computers.

### After each migration

New tables created by a migration are owned by whoever ran it and are not covered
by the grants above. After `alembic upgrade head`, re-run the `GRANT … ON ALL TABLES`
and `REVOKE` statements, or set default privileges once:

```sql
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pesticide_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO pesticide_app;
```

## 2. Operations that need the owner login

These are administrative and should be run by the owner from a trusted machine
with an `.env` that holds the database owner's credentials, not the app login:

| Operation | Why |
|---|---|
| `alembic upgrade head` (and the in-app "Upgrade database now?" prompt) | Needs `CREATE`/`ALTER` on the schema |
| Restore from backup (`pg_restore`) | Recreates tables and rows |
| CSV import with **replace** (`scripts.import_csv_export --replace`, Settings > Backup) | Deletes every existing row, including the ledgers |

With the restricted login these operations fail with a permission error instead of
changing anything. That is the intended behaviour. Backups (`pg_dump`) work with
the restricted login, because it can read every table.

## 3. Network exposure

`docker-compose.yml` publishes PostgreSQL on every network interface. On a shop network:

- Bind the published port to the interface the tills use, or to `127.0.0.1` when
  everything runs on one computer: `"127.0.0.1:5440:5432"`.
- Allow the PostgreSQL port only from the shop computers in the host firewall
  (`pg_hba.conf` and the OS firewall). Use `DATABASE_SSL_MODE=require` whenever the
  database is not on the same computer.

## 4. Checklist

- [ ] `.env` on shop computers uses `pesticide_app`, not a superuser
      (`SELECT rolsuper FROM pg_roles WHERE rolname = current_user;` returns `f`).
- [ ] `UPDATE payments SET amount = amount WHERE false;` fails with
      `permission denied` when run as `pesticide_app`.
- [ ] `.env` is readable only by the shop account (`chmod 600` on Linux; restricted
      ACL on Windows).
- [ ] The PostgreSQL port is not open to the whole LAN.
- [ ] `APP_ENV=production` on shop computers, which turns on the production-only
      configuration checks.
