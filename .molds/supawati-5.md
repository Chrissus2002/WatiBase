<!-- molds
id: supawati-5
stage: build
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-22T21:54:17Z
updated_at: 2026-04-22T22:05:09Z
closed_at: 
defer_until: 
code_branch: 
-->


# Backfill messages using DB contacts instead of Wati API

## TL;DR

- `BatchMessageImport` currently calls `wati_client.get_contacts()` to get phone numbers, then fetches messages per contact — this is redundant since contacts are already in `canonical.contacts`
- Change the backfill to read phones from the database instead, skipping the extra Wati API round-trip

## Problem

`BatchMessageImport.run()` starts by calling `self.wati_client.get_contacts(max_pages=limit)` to get the list of phone numbers to fetch messages for. But by the time we run `backfill`, contacts have already been synced into `canonical.contacts` via `sync contacts`. Fetching them again from Wati is slow (paginated, rate-limited) and unnecessary.

## Solution

Replace the `wati_client.get_contacts()` call in `BatchMessageImport.run()` (line 16) with a Supabase query on `canonical.contacts`:

```python
query = client.schema("canonical").table("contacts").select("phone_raw").eq("tenant_id", self.db_tenant_id)
if limit:
    query = query.limit(limit)
result = query.execute()
contacts = result.data
```

Then the per-contact loop changes from:
```python
phone = contact.get("wAid") or contact.get("phone") or contact.get("whatsappNumber")
```
to:
```python
phone = contact["phone_raw"]
```

The `phone_raw` column stores the original Wati phone value (e.g. the `wAid`), which is what the Wati `/api/v1/getMessages/{phone}` endpoint expects.

Everything else stays the same — message fetching, staging upsert, promotion via RPC, job lifecycle.

## Files to Modify
| File | Action | Done | Purpose |
|------|--------|---------|
| `shared/batch_message_import.py` | Modify | yes | Replace `wati_client.get_contacts()` with DB query on `canonical.contacts`; simplify phone extraction |

## Verification

- `backfill --tenant-id <id> --help` still shows `--tenant-id` and `--limit`
- `backfill --tenant-id <id> --limit 5` fetches messages for at most 5 contacts from the DB
- Contacts with no `phone_raw` are skipped with a warning
- The rest of the pipeline (staging upsert, promotion, job lifecycle) is unchanged

## Implementation Notes

- `phone_raw` stores the original Wati phone (from `wAid`, `phone`, or `whatsappNumber`) — this is the value the Wati messages API expects in the URL path
- `limit` changes meaning from "max Wati contact pages" to "max contacts from DB" — update the CLI help text accordingly
- No schema or DB function changes needed — only the Python class changes
