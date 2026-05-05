# Database

## Schema overview

The database uses four schemas:

- `public` -- tenants and connector configuration
- `staging` -- raw provider data (append-only for messages, upsert for contacts)
- `canonical` -- normalized, provider-agnostic records
- `ops` -- import jobs, webhook logs, and processing status
- `analytics` -- downstream reporting (planned)

## File layout

| File | Purpose |
|------|---------|
| `init_db.sql` | Full current schema. Represents a fresh install of the database. |
| `functions.sql` | All SQL functions and RPCs. |
| `drop_db.sql` | Teardown script for a full reset. |
| `migrations/` | Ordered migration files tracking incremental schema changes. |

## Convention

`init_db.sql` is the single source of truth for the current database structure. When adding a migration in `migrations/`, always update `init_db.sql` to reflect the same change so it stays in sync with the live schema.

Migration files should be named with a sequential prefix and a short description, e.g. `001_add_analytics_summary.sql`.
