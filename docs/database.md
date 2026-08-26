# Database

The project uses its own PostgreSQL databases and Docker volumes. Development listens on port
`5440`; the disposable test database listens on `5441`. The project owns its credentials, ports,
database names, and Docker volumes.

Core relationships:

- `products` → `purchase_items` → `stock_batches` → `stock_movements`
- `suppliers` → `purchases` and batch intake
- `customers` or `dealers` → `sales` → `sale_items`
- `sales` and `purchases` → immutable `payments`
- all privileged operations → immutable `audit_logs`
- sales and scheduled reports → persistent `email_history`

Each change is managed by Alembic. Run `alembic check` after model edits and never edit a deployed
schema manually. Pytest refuses any database whose name does not end in `_test`.
