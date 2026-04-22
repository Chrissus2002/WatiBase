<!-- molds
id: supawati-2
stage: closed
resume_stage:
parent:
deps:
priority: P2
created_at: 2026-04-21T17:46:21Z
updated_at: 2026-04-21T17:46:21Z
closed_at: 2026-04-21T00:00:00Z
defer_until:
code_branch:
-->

# Create batch contact import in shared directory

## TL;DR

- Create `shared/batch_contact_import.py` — fetches all contacts from Wati,
  upserts into `staging.contacts_raw_data`, then promotes to `canonical.contacts`
  and `canonical.contact_provider_keys`
- Wire up the placeholder `sync contacts` CLI command to call it
- Fix three SQL reference bugs in `database/init_db.sql` that will break the
  import at runtime

## Problem

`WatiClient.get_contacts()` fetches paginated contacts from the Wati API.
`SupabaseClient` wraps the Supabase connection. There is no module yet that
bridges the two — writing Wati contacts into the staging and canonical layers.
The CLI `sync contacts` command is scaffolded but unimplemented (`_placeholder`).

## Solution

Create `shared/batch_contact_import.py` with a `BatchContactImport` class that:

1. Fetches all contacts from `WatiClient.get_contacts()`
2. Upserts each contact into `staging.contacts_raw_data`
   - fields: `tenant_id`, `wati_contact_id`, `raw_data`, `last_seen_at`
   - upsert on `(tenant_id, wati_contact_id)` via the unique constraint
3. Promotes each contact into `canonical.contacts`
   - upsert on `(tenant_id, phone_normalized)` — this is the unique constraint
   - see field mapping below
4. Upserts the provider key mapping into `canonical.contact_provider_keys`
   - upsert on `(tenant_id, provider, provider_contact_id)`

Wire up `sync contacts` in the CLI to accept `--tenant-id` and call this module.

### Actual `canonical.contacts` schema (as built)

The table in `init_db.sql` differs from the original mold spec:

| DB column | Wati source | Notes |
|-----------|-------------|-------|
| `tenant_id` | passed in as parameter | DB bigint, not Wati account string |
| `phone_normalized` | `wAid` / `phone` / `whatsappNumber` stripped of non-digits | NOT NULL — required for upsert key |
| `phone_raw` | `wAid` / `phone` / `whatsappNumber` | raw as received |
| `name` | `fullName` or `name` | nullable |
| `source` | hardcode `'wati'` | |
| `wati_contact_id` | `id` field from Wati | already on canonical.contacts |
| `is_canonical` | default `true` | |

`phone_normalized` is NOT NULL and is part of the unique key
`UNIQUE(tenant_id, phone_normalized)`. Since full phone normalisation is out of
scope, strip all non-digit characters from the raw phone field as a pragmatic
stand-in. Skip contacts with no recoverable phone value.

### Wati contact field lookup order

Wati returns inconsistent field names across API versions. Use this priority:
1. `wAid` — most reliable WhatsApp ID
2. `phone`
3. `whatsappNumber`

For name: `fullName` first, then `name`.
For Wati contact ID: `id` field.

### CLI wiring

`wati_conn` is already instantiated globally in `wati_supabase_cli.py` (line 25).
The `sync contacts` parser needs a `--tenant-id` argument added before the
handler can be wired up.

### SQL bugs to fix in `init_db.sql`

These will cause runtime failures and must be fixed in the same PR:

1. `canonical.contacts` references `public.tenants` — should be `public.tenant`
2. `canonical.messages` references `silver.contacts(contact_id)` — should be
   `canonical.contacts(contact_id)`
3. `canonical.messages` references `public.tenants` and `public.import_jobs` —
   should be `public.tenant` and `ops.import_jobs`
4. `public.tenant_connectors` has a trailing comma before `)` — syntax error

## Files to Modify

| File | Action | Purpose |
|------|--------|---------|
| `shared/batch_contact_import.py` | create | Fetch from Wati, write to staging + canonical |
| `scripts/wati_supabase_cli.py` | modify | Add `--tenant-id` to `sync contacts`, wire handler |
| `database/init_db.sql` | fix | Correct four SQL reference/syntax bugs |

## Verification

- `./wati-supabase sync contacts --tenant-id <id>` completes without error
- `staging.contacts_raw_data` rows are upserted for the tenant
- `canonical.contacts` rows exist with `phone_normalized` populated
- `canonical.contact_provider_keys` rows link Wati IDs to canonical contacts
- Re-running the import does not duplicate rows
- Contacts with no phone value are skipped with a logged warning

## Implementation Notes

- `WatiClient` takes the Wati account ID string (from `WATI_ACCOUNT_ID` env),
  not the DB `tenant_id` bigint — keep variable names distinct
- `wati_conn` is already a module-level global in the CLI — do not reinstantiate
- Phone normalisation: `re.sub(r'\D', '', raw_phone)` is sufficient for this
  mold. Full `phonenumbers` library normalisation is a separate enrichment task
- Batch size: upsert contacts in chunks (e.g. 100 at a time) to avoid hitting
  Supabase request size limits
- Do not implement `ops.import_jobs` tracking in this mold
