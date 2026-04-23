<!-- molds
id: supawati-7
stage: qa
resume_stage:
parent:
deps:
priority: P2
created_at: 2026-04-22T23:53:47Z
updated_at: 2026-04-22T23:53:47Z
closed_at:
defer_until:
code_branch:
-->

# Resumable message import with progress tracking

## TL;DR

Make `backfill` resumable: add a `--resume <job_id>` flag that reuses an existing failed/interrupted job, and skip contacts whose messages are already fully staged. The background subprocess spawner must forward the flag to the child process.

## Problem

`BatchMessageImport.run()` iterates over all canonical contacts and fetches messages per phone. If the background process is killed at contact 150 of 500:
- `_check_existing_backfill` marks the stale job as `failed`
- A new run creates a new job and re-fetches messages for all 500 contacts
- Staging upsert is idempotent (`on_conflict tenant_id,wati_message_id`), so no duplicates — but all Wati API calls are repeated

For large tenants this makes message backfill fragile — a single API error, timeout, or `kill` forces a full API re-fetch.

## Solution

### 1. Add `resume_job_id` parameter to `BatchMessageImport.run()`

When `resume_job_id` is provided:
- Skip creating a new `ops.import_jobs` row — reuse the existing one
- Validate it belongs to this tenant and is `failed` or `running`
- Set it back to `status='running'`, clear `error_message`
- Use it as `batch_id` for new staging rows

When not provided, current behavior: create a new job.

### 2. Skip contacts that are already staged

Before calling `wati_client.get_messages(phone)`, check if this phone already has messages in staging for this tenant:

```python
exists = (
    client.schema("staging").table("messages_raw_data")
    .select("id", count="exact")
    .eq("tenant_id", self.db_tenant_id)
    .eq("conversation_id", phone)
    .limit(1)
    .execute()
)
if exists.count > 0:
    print(f"[{idx}/{total}] Skipping {phone} — already staged")
    continue
```

**Note on partial contacts:** If the process crashed mid-contact (e.g., 300 of 1000 messages staged for one phone), the skip check would still show rows exist and skip that contact. This is acceptable because:
- Partial data is still valuable
- Re-fetching all messages for one contact to catch the missing 700 adds complexity with marginal benefit
- The staging upsert is idempotent, so a manual full re-run still works if needed

This skip logic runs on every backfill (not just `--resume`), since re-staging contacts that are already done is always wasteful.

### 3. Wire `--resume` through the CLI and background process

- Add `--resume` argument to the `backfill` parser
- In `handle_backfill_messages`:
  - Parent process: pass `--resume <job_id>` to the child `cmd` list
  - When `--resume` is provided, skip `_check_existing_backfill` (the user is explicitly resuming a known job)
  - Child process: pass `resume_job_id` to `BatchMessageImport.run()`

### 4. `_check_existing_backfill` adjustment

No change needed. When a process dies, it marks the job `failed`. When the user runs `--resume <job_id>`, they're explicitly picking up that failed job. The resume path bypasses `_check_existing_backfill` because the user already knows which job they want.

## Files to Modify
| File | Action | Purpose | Done |
|------|--------|---------|------|
| `shared/batch_message_import.py` | Modify | Add `resume_job_id` param, reuse existing job when set, add skip-check before each contact | yes |
| `scripts/wati_supabase_cli.py` | Modify | Add `--resume` arg to `backfill` parser, forward to child process and `BatchMessageImport` | yes |

## Verification

- Run `backfill --tenant-id <id> --limit 1` — creates a new job, stages messages
- Kill the background process mid-run (use `kill <pid>`)
- Run `backfill --tenant-id <id>` — `_check_existing_backfill` marks stale job as failed, starts fresh
- Note the failed `job_id` from `ops.import_jobs`
- Run `backfill --tenant-id <id> --resume <job_id>` — should skip already-staged contacts, re-use the job row, mark completed at end
- Query `select job_id, status, processed_count from ops.import_jobs where job_id = '<job_id>'` — should show `completed`
- Run `backfill --tenant-id <id>` again (no --resume) — the skip-check should skip all contacts that are already staged, making it fast

## Implementation Notes

- The skip-check uses `conversation_id` in staging which is set to `msg.get("conversationId") or str(phone)` — this matches the `phone_raw` value used to iterate contacts
- `processed_count` continues to be updated after each contact (line 105-107 in current code), giving the `watch status` command live progress
- The `--resume` flag works independently of `--limit` — both can be combined
- When resuming, the `total_count` on the job may differ if contacts were added since the original run — this is fine, it reflects the current state
