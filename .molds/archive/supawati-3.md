<!-- molds
id: supawati-3
stage: closed
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-21T17:47:16Z
updated_at: 2026-04-22T22:18:27Z
closed_at: 2026-04-22T22:18:27Z
defer_until: 
code_branch: 
-->




# Batch import for messages

## TL;DR

- Create `shared/batch_message_import.py` with a `BatchMessageImport` class mirroring `BatchContactImport`
- Fetches all contacts, pulls messages per contact, stages in `staging.messages_raw_data`, promotes to `canonical.messages` via a new DB function
- Add `process_messages_to_canonical` to `database/functions.sql`
- Wire the `backfill messages` CLI placeholder to call it with `--tenant-id`
- Fix `get_messages` pagination bug (overwrite → extend)

## Problem

The CLI has a placeholder `backfill messages` command that prints "not implemented". `BatchContactImport` exists in `shared/` and handles the full contact pipeline (Wati → staging → canonical), but the message equivalent has never been built.

## Solution

### Pre-requisite fix: `shared/wati_client.py`

`get_messages` has a pagination bug at line 91:
```python
all_messages[phone_number] = data.get("messages")  # overwrites each page
```
Change to:
```python
all_messages[phone_number].extend(data.get("messages") or [])
```

### New class: `shared/batch_message_import.py`

Mirror `shared/batch_contact_import.py` exactly:

```python
class BatchMessageImport:
    def __init__(self, supabase_client: SupabaseClient, wati_client: WatiClient, db_tenant_id: int):
        self.supabase_client = supabase_client
        self.wati_client = wati_client
        self.db_tenant_id = db_tenant_id

    def run(self, limit: int = None) -> dict:
        ...
```

`run()` steps (matching `BatchContactImport.run()`):

1. **Fetch contacts** — `self.wati_client.get_contacts(max_pages=limit)` to get phone numbers
2. **Create import job** — insert into `ops.import_jobs` with `job_type='message_backfill'`, `status='running'`, `total_count=len(contacts)`, `started_at=now()`; capture `job_id` as `batch_id`
3. **Per-contact message fetch + staging upsert** — for each contact:
   - Get phone: `contact.get("wAid") or contact.get("phone") or contact.get("whatsappNumber")`
   - Call `self.wati_client.get_messages(phone)` → flatten: `result.get(phone, [])`
   - Build staging rows in batches of 100, upsert into `staging.messages_raw_data`:
     - `tenant_id`, `batch_id`, `wati_message_id`: `str(msg["id"])`, `conversation_id`: `msg.get("conversationId") or phone`, `raw_data`: full msg dict
     - Upsert on `tenant_id,wati_message_id`
   - Update `processed_count` on the job row after each contact
   - `time.sleep(2)` between contacts for rate limiting
4. **Promote to canonical** — call `rpc("process_messages_to_canonical", {"p_batch_id": batch_id})`
5. **Mark job completed/failed** — same try/except pattern as `BatchContactImport`
6. **Return stats dict** — `{"batch_id", "total", "upserted_staging", "upserted_canonical"}`

### New DB function: `database/functions.sql`

Add `process_messages_to_canonical(p_batch_id uuid)` mirroring `process_contacts_to_canonical`:

- Read from `staging.messages_raw_data` where `batch_id = p_batch_id`
- Join to `canonical.contacts` to resolve `contact_id` — match on `tenant_id` and `wati_contact_id` (from the staging row's `raw_data` or the contact's phone)
- Insert into `canonical.messages` with field mapping from `raw_data` JSONB:
  - `wati_message_id` ← staging `wati_message_id`
  - `message_content` ← `raw_data->>'text'` or `raw_data->>'body'`
  - `direction` ← `raw_data->>'type'` (Wati uses `'outbound'`/`'inbound'`)
  - `message_type` ← `raw_data->>'messageType'` or `'text'`
  - `timestamp` ← `(raw_data->>'timestamp')::timestamptz` or `(raw_data->>'created')::timestamptz`
  - `conversation_id` ← staging `conversation_id`
  - `batch_id` ← `p_batch_id`
- `ON CONFLICT (tenant_id, wati_message_id) DO NOTHING`
- Skip messages where no matching canonical contact exists
- Return `jsonb_build_object('messages_processed', count, ...)`

### CLI wiring

In `scripts/wati_supabase_cli.py`:

- Add `from batch_message_import import BatchMessageImport` at top
- Replace `_placeholder(backfill_messages_parser, "backfill messages")` with:
  - Add `--tenant-id` (required) and `--limit` arguments
  - Set handler to `handle_backfill_messages`
- `handle_backfill_messages`: mirror `handle_sync_contacts` — create `BatchMessageImport(client, wati_conn, args.tenant_id)`, call `importer.run(limit=args.limit)`, print stats

## Files to Modify

| File | Action | Done | Purpose |
|------|--------|------|---------|
| `shared/wati_client.py` | Fix bug | yes | Line 91: extend instead of overwrite per page |
| `shared/batch_message_import.py` | Create | yes | `BatchMessageImport` class mirroring `BatchContactImport` |
| `database/functions.sql` | Modify | yes | Add `process_messages_to_canonical(p_batch_id)` function |
| `scripts/wati_supabase_cli.py` | Modify | yes | Wire `backfill messages` with `--tenant-id`, `--limit`, handler |

## Verification

- `./wati-supabase backfill messages --tenant-id <id>` completes without error
- `staging.messages_raw_data` rows are upserted for the tenant
- `canonical.messages` rows exist with `contact_id` resolved
- Re-running the import does not duplicate rows (idempotent)
- Contacts with no phone value are skipped with a logged warning
- Mid-run exception → job status is `failed` with `error_message` set

## Implementation Notes

- Mirror `BatchContactImport` structure exactly: same constructor signature, same `run()` return shape, same job lifecycle pattern
- `conversation_id` is `NOT NULL` in staging — use phone as fallback if Wati message dict lacks a conversation ID
- Batch size 100 for staging upserts, matching contact import
- `limit` parameter caps `max_pages` on `get_contacts()` (for testing), same as contact import
- Phone field lookup order: `wAid` → `phone` → `whatsappNumber` (same as contact import)
- The `process_messages_to_canonical` function should join `staging.messages_raw_data` to `canonical.contacts` via phone matching (using `normalize_phone` on the staging `conversation_id` or `raw_data` phone field) — contacts must be imported first
