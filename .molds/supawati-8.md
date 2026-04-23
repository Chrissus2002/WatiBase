<!-- molds
id: supawati-8
stage: design
resume_stage:
parent: 
deps: 
priority: P2
created_at: 2026-04-23T16:05:46Z
updated_at: 2026-04-23T16:05:46Z
closed_at:
defer_until:
code_branch:
-->

# Fix message backfill resume to skip by processed count

## TL;DR

The `--resume` flag reused the job but still iterated from contact 1, relying on per-contact staging DB queries to skip. Fixed: read `processed_count` from the job, skip directly to contact N+1, and add `ORDER BY phone_normalized` so the contact list is deterministic between runs.

## Problem

supawati-7 added `--resume` but it only reused the `ops.import_jobs` row. It still looped through all contacts and ran a `staging.messages_raw_data` existence query per contact to decide whether to skip. This meant:
- N extra DB round trips on resume (one per already-processed contact)
- The contacts query had no `ORDER BY`, making index-based skipping unsafe
- `processed_count` was already being tracked but never used for resume

## Solution

1. Add `.order("phone_normalized")` to the contacts query so the list is stable between runs
2. On resume, fetch `processed_count` from the job row and skip contacts where `idx <= processed_count`
3. Remove the per-contact staging skip-check — no longer needed
4. `processed_count` continues to update with the absolute contact index after each contact

Already implemented in `shared/batch_message_import.py`.

## Files to Modify
| File | Action | Purpose | Done |
|------|--------|---------|------|
| `shared/batch_message_import.py` | Modify | Add ORDER BY, skip by processed_count, remove staging skip-check | yes |

## Verification

- Run `backfill --tenant-id <id>`, kill mid-run at contact ~5
- Check `select processed_count from ops.import_jobs` — should be ~5
- Run `backfill --tenant-id <id> --resume <job_id>` — should print "Resuming from contact 6" and skip 1-5 instantly (no DB queries per skipped contact)
- Completed job should show `processed_count` equal to total contacts

## Implementation Notes

- Deterministic ordering is critical — without `ORDER BY`, contact positions could shuffle between runs, causing the index skip to process the wrong contacts
- `processed_count` on completion is set to `total` (not `upserted_canonical`) to reflect all contacts were visited
