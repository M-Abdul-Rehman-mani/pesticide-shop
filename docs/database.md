# Database

The project uses its own PostgreSQL databases and Docker volumes. Development listens on port
`5440`; the disposable test database listens on `5441`. The project owns its credentials, ports,
database names, and Docker volumes.

Core relationships:

- `products` → `purchase_items` → `stock_batches` → `stock_movements`
- `suppliers` → `purchases` and batch intake
- `customers` or `dealers` → `sales` → `sale_items`
- `sales` and `purchases` → immutable `payments`
- `dealers` → `payments.dealer_id`, which carries a dealer's whole account history: the
  allocations that settle their individual invoices, and any surplus held as account credit with no
  document attached
- all privileged operations → immutable `audit_logs`
- sales and scheduled reports → persistent `email_history`

A payment therefore satisfies one of two shapes, enforced by
`ck_payments_exactly_one_document`: it settles exactly one sale or one purchase, or it is dealer
account credit with `dealer_id` set and no document. A dealer's balance may go negative when they
pay ahead.

Each change is managed by Alembic. Run `alembic check` after model edits and never edit a deployed
schema manually. Pytest refuses any database whose name does not end in `_test`.
