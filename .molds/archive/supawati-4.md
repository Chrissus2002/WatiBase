<!-- molds
id: supawati-4
stage: closed
resume_stage:
parent: 
deps: 
priority: P2
created_at: 2026-04-21T22:24:56Z
updated_at: 2026-04-21T22:24:56Z
closed_at: 2026-04-21T22:34:41Z
defer_until:
code_branch:
-->

# Give each batch import a batch id

## TL;DR

Each contact import run should create a row in `ops.import_jobs` and pass that job's UUID as `batch_id` to both the staging upsert and the canonical promotion, so every record can be traced back to the import that produced it.

## Problem

Currently `BatchContactImport.run()` upserts contacts into `staging.contacts_raw_data` and `canonical.contacts` without a `batch_id`. The `batch_id` columns exist on both tables (FK to `ops.import_jobs`) but are never populated. This means there is no way to tell which import run produced a given row, making it impossible to call `staging.process_contacts_to_canonical(p_batch_id)` or audit/replay a specific batch.

## Solution

1. At the start of `run()`, insert a row into `ops.import_jobs` with `job_type='contact_import'` and `status='running'`, capturing the returned `job_id` as `batch_id`.
2. Pass `batch_id` into every staging upsert row.
3. Pass `batch_id` into every canonical upsert row.
4. On success, update the `ops.import_jobs` row to `status='completed'` and set `processed_count` and `completed_at`.
5. On any exception, update the row to `status='failed'` and store the error in `error_message`.

## Files to Modify
| File | Action | Purpose | Done |
|------|--------|---------|------|
| `shared/batch_contact_import.py` | Modify | Create import job at start, pass batch_id through, mark completed/failed | yes |

## Verification

- Run `sync contacts --tenant-id <id> --limit 1`
- Query `select job_id, status, processed_count from ops.import_jobs` — should have one row with `status='completed'`
- Query `select batch_id from staging.contacts_raw_data limit 5` — should match the job_id
- Query `select batch_id from canonical.contacts limit 5` — should match the job_id

## Implementation Notes

- `ops.import_jobs` has `total_count` (set it to `len(contacts)` after fetching) and `processed_count` (set at end).
- Use a try/except around the main loop to ensure the job is always marked failed on error.
- `batch_id` column in staging is nullable so existing rows without a batch_id are fine.
