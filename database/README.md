# Database

## Schema overview

The database uses four schemas:

- `public` -- tenants and connector configuration
- `staging` -- raw provider data (append-only for messages, upsert for contacts)
- `canonical` -- normalized, provider-agnostic records
- `ops` -- import jobs, webhook logs, and processing status

## File layout

| File | Purpose |
|------|---------|
| `init_db.sql` | Full current schema. Represents a fresh install of the database. |
| `functions.sql` | All SQL functions and RPCs. |
| `drop_db.sql` | Teardown script for a full reset. |

## Architecture

### Schemas

| Schema | Purpose |
|--------|---------|
| `public` | Tenant identity (`tenant`) and connector credentials (`tenant_connectors`) |
| `staging` | Raw Wati data. Contacts are upserted (latest snapshot wins), messages are append-only |
| `canonical` | Normalized, provider-agnostic records with E.164 phone identities |
| `ops` | Job tracking, webhook logs, and processing status |
| `analytics` | Reserved for downstream reporting (currently empty) |

### Tables

**public**
- `tenant` -- one row per workspace (keyed by `tenant_id`)
- `tenant_connectors` -- Wati API credentials per tenant (one active connector per tenant)

**staging**
- `contacts_raw_data` -- latest-seen contact snapshots from Wati (upserted by `tenant_id` + `wati_contact_id`)
- `messages_raw_data` -- raw message events (append-only, unique by `tenant_id` + `wati_message_id`)

**canonical**
- `contacts` -- one row per contact identity, with E.164 phone fields
- `contact_provider_keys` -- maps Wati contact IDs to canonical contacts
- `messages` -- one row per message, linked to a canonical contact

**ops**
- `import_jobs` -- tracks sync and backfill jobs (status, progress, resume point)
- `daemon_task_runs` -- daemon health and status
- `webhook_logs` -- raw webhook payloads for replay/debug
- `webhook_processing_log` -- processing outcome per webhook event
- `webhook_processing_status` -- per-tenant webhook health counters

### Functions (in `functions.sql`)

| Function | Called by | Purpose |
|----------|-----------|---------|
| `normalize_phone(text)` | Other functions | Strips non-digits, fixes Mexican mobile numbers |
| `process_contacts_to_canonical(batch_id)` | `sync contacts` | Promotes staged contacts to canonical |
| `process_messages_to_canonical(batch_id)` | `backfill` | Promotes staged messages to canonical |
| `process_webhook_message_to_canonical(staging_id)` | Webhook edge function | Promotes a single webhook message to canonical |
| `update_webhook_status(tenant_id, status)` | Webhook edge function | Increments per-tenant webhook health counters |

### Data flow

```
Wati API
  │
  ▼
staging (raw snapshots)
  │
  ▼  process_contacts_to_canonical() / process_messages_to_canonical()
canonical (normalized, deduplicated)
```

Contacts and messages follow the same pattern: raw data lands in staging, then a database function promotes it to canonical. The CLI and the webhook edge function both feed into this pipeline.

## Convention

`init_db.sql` is the single source of truth for the current database structure. If you change the schema, update `init_db.sql` so it stays in sync with the live database.
