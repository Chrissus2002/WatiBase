<!-- molds
id: supawati-3
stage: design
resume_stage:
parent:
deps:
priority: P2
created_at: 2026-04-21T17:47:16Z
updated_at: 2026-04-21T17:50:00Z
closed_at:
defer_until:
code_branch:
-->

# Batch import for messages

## TL;DR

- Create `scripts/batch_messages_import.py`: fetch all contacts from Wati, pull messages per contact, stage in `staging.messages_raw_data`, promote to `canonical.messages`
- Job lifecycle tracked in `ops.import_jobs` (`pending` → `running` → `completed`/`failed`)
- Fix a pre-existing pagination bug in `WatiClient.get_messages` (page results are overwritten, not accumulated)
- Wire the `backfill messages` CLI placeholder to call this script with `--tenant-id`

## Problem

The CLI has a placeholder `backfill messages` command that prints "not implemented". No script performs a bulk historical import of messages from Wati into Supabase. The DB schema already supports this (`staging.messages_raw_data`, `canonical.messages`, `ops.import_jobs`) but the script has never been built.

## Solution

### Pre-requisite fix: `shared/wati_client.py`

`get_messages` has a pagination bug at line 66:
```python
all_messages[phone_number] = data.get("messages")  # overwrites each page
```
Change to:
```python
all_messages[phone_number].extend(data.get("messages") or [])
```
Without this fix, only the last page of messages is kept for any contact with more than `page_size` messages.

### New script: `scripts/batch_messages_import.py`

Steps:

1. **Create job** — insert into `ops.import_jobs`:
   - `job_type='message_backfill'`, `status='running'`
   - `started_at=now()`, `subprocess_pid=os.getpid()`
   - `tenant_id` from CLI arg
   - Capture returned `job_id` as `batch_id` for all staging rows

2. **Fetch contacts** — `WatiClient.get_contacts()` → list of contact dicts.
   - Set `total_count=len(contacts)` on the job row.
   - Phone for `get_messages()` call: `contact.get("wAid") or contact.get("phone") or contact.get("whatsappNumber")`

3. **Per-contact message fetch** — for each contact, call `WatiClient.get_messages(phone)`.
   - Returns `{phone: [msg, ...]}`. Flatten: `messages = result.get(phone, [])`.
   - For each message, upsert into `staging.messages_raw_data`:
     - `wati_message_id`: `msg.get("id")`
     - `conversation_id`: `msg.get("conversationId") or msg.get("conversation_id") or phone` (phone as fallback)
     - `raw_data`: full message dict as JSONB
     - `batch_id`, `tenant_id`
     - Conflict: `ON CONFLICT (tenant_id, wati_message_id) DO NOTHING`
   - After staging, resolve `contact_id` from `canonical.contacts` where `wati_contact_id = contact["id"]` (or fall back to `phone_normalized = phone`). If not found, skip and log warning.
   - Bulk-insert resolved messages into `canonical.messages` — `ON CONFLICT (tenant_id, wati_message_id) DO NOTHING`.

4. **Progress** — after each contact, `UPDATE ops.import_jobs SET processed_count = processed_count + 1 WHERE job_id = <batch_id>`.

5. **Finish** — on success: `status='completed', completed_at=now()`. On unhandled exception: `status='failed', error_message=str(exc)`.

### Column names — schema ground truth

Use these column names (not the names the CLI status display uses, which has a mismatch):
- `ops.import_jobs`: `processed_count`, `total_count` (CLI incorrectly reads `processed_messages`/`total_messages`)

### Schema inconsistency

`canonical.messages` line 120 references `silver.contacts(contact_id)` — `silver` schema does not exist. At implementation time, use `canonical.contacts` as the FK target (matches actual schema). The DDL has a typo; do not add a `silver` schema.

### CLI integration

In `scripts/wati_supabase_cli.py`, replace the `_placeholder` on `backfill messages`:
- Add `--tenant-id` required argument to `backfill_messages_parser`
- Implement `handle_backfill_messages(args)` that imports and calls `batch_messages_import.run(tenant_id=args.tenant_id)`
- Export a `run(tenant_id)` entry point from the script

## Files to Modify

| File | Action | Purpose |
|------|--------|---------|
| `shared/wati_client.py` | Fix bug | Line 66: extend instead of overwrite `all_messages[phone_number]` per page |
| `scripts/batch_messages_import.py` | Create | Job lifecycle, Wati fetch, staging upsert, canonical promotion |
| `scripts/wati_supabase_cli.py` | Modify | Replace placeholder on `backfill messages`, add `--tenant-id`, call `batch_messages_import.run()` |

## Verification

1. `python scripts/batch_messages_import.py --tenant-id <id>` — job row in `ops.import_jobs` reaches `completed`, rows appear in `staging.messages_raw_data` and `canonical.messages`
2. `python scripts/wati_supabase_cli.py backfill messages --tenant-id <id>` — delegates to script, prints per-contact progress, exits 0
3. Re-running is idempotent — duplicate `wati_message_id` rows silently skipped in both staging and canonical
4. Mid-run Wati exception → job status is `failed` with `error_message` set
5. `python scripts/wati_supabase_cli.py status --tenant-id <id>` shows the job in the active or completed state (validates `ops.import_jobs` columns are written correctly)

## Implementation Notes

- Use `sys.path` manipulation matching `wati_supabase_cli.py:16` to import from `shared/`
- Rate-limit sleep: add `time.sleep(2)` between contacts (Wati's documented limit is ~5 req/s; the contact fetcher uses `sleep(5)` between pages)
- `conversation_id` is `NOT NULL` in staging — use phone as fallback if the Wati message dict has no conversation ID field
- Wati contact ID field: `contact.get("id")` for `wati_contact_id` lookup; phone for `get_messages()` call
- `canonical.messages` canonical field mapping from Wati message dict:
  - `wati_message_id` ← `msg["id"]`
  - `message_content` ← `msg.get("text") or msg.get("body")`
  - `direction` ← `msg.get("type")` (Wati uses `"outbound"` / `"inbound"`)  → map to `'outbound'/'inbound'`
  - `message_type` ← `msg.get("messageType")` or `"text"` as default
  - `timestamp` ← `msg.get("timestamp")` or `msg.get("created")` (must be `TIMESTAMPTZ`)
  - `conversation_id` ← `msg.get("conversationId")` or phone fallback
