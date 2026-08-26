# Architecture

Pesticide Shop Manager is a native PySide6 desktop application backed by PostgreSQL. The UI calls
transactional services through short-lived SQLAlchemy sessions. Redis and Celery handle email,
scheduled owner reports, and backups without delaying checkout.

The domain is pesticide-specific: products, suppliers, batch/expiry stock, customers, dealers,
purchases, sales, payments, settings, users, email history, and audit events. Purchases add exact
batches; sales lock and decrement those batches atomically. Stock movements, payments, and audit
events are append-only in both application code and PostgreSQL triggers.

Permissions are enforced in services as well as navigation. All currency uses fixed-point Decimal
and PostgreSQL NUMERIC values. Shop profile and SMTP settings are database-backed, with secrets
encrypted using the stable application secret.
