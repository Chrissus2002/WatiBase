<!-- molds
id: supawati-7
stage: design
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

Track how many messages have been processed per contact so that if an import is interrupted, it can resume from where it left off instead of re-fetching everything.

## Problem

Message imports fetch all messages for every contact from the Wati API. If the process is interrupted (timeout, crash, API rate limit), there is no record of which contacts have already been fully processed. Re-running the import starts from scratch, re-fetching messages for contacts that were already completed.

## Solution

## Files to Modify
| File | Action | Purpose |
|------|--------|---------|

## Verification

## Implementation Notes

- `ops.import_jobs` already tracks `processed_count` — this could be updated incrementally per contact
- `staging.messages_raw_data` has `(tenant_id, wati_message_id)` uniqueness, so re-inserting existing messages is safe but wasteful at API level
- The resume logic should check which contacts already have messages in staging for the current or a prior batch and skip them
