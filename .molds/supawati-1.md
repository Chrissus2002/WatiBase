<!-- molds
id: supawati-1
stage: build
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-21T16:23:00Z
updated_at: 2026-04-21T17:26:41Z
closed_at: 
defer_until: 
code_branch: 
-->


# CLI download contacts and messages locally

## TL;DR

- Add a `to-file` command group to the CLI with two subcommands: `contacts` and `messages`
- `to-file contacts` calls the existing `WatiClient.get_contacts()` and writes JSON to a file
- `to-file messages` iterates contacts (or a single phone number) and writes messages via `WatiClient.get_messages()`
- All logic lives in `scripts/wati_supabase_cli.py`; no new files needed

## Problem

The CLI (`scripts/wati_supabase_cli.py`) can read Wati data but has no way to persist it locally. `sync contacts` and `backfill messages` are scaffolded as placeholders that print "not implemented". Users cannot export contacts or message history to disk for offline inspection, backups, or debugging.

## Solution

Add a `to-file` top-level command group with two subcommands:

```
wati-supabase to-file contacts [--output FILE] [--format json]
wati-supabase to-file messages --phone PHONE [--output FILE]
wati-supabase to-file messages --all [--output-dir DIR]
```

**`to-file contacts`**
- Calls `wati_conn.get_contacts()` (already paginated)
- Writes result as JSON array to `--output` (default: `contacts.json`)
- Prints count on success

**`to-file messages --phone PHONE`**
- Calls `wati_conn.get_messages(phone_number)` for a single contact
- Writes JSON to `--output` (default: `messages_<phone>.json`)

**`to-file messages --all`**
- Fetches all contacts first, then iterates and calls `get_messages()` per contact
- Writes one file per contact to `--output-dir` (default: `./messages/`)
- Prints progress per contact

No new files. All handlers added to `scripts/wati_supabase_cli.py`.

### Bug to fix during implementation

`wati_client.py:59` — `get_messages` URL uses `?page=` instead of `&page=` for the second query parameter. Fix it while touching the file, otherwise pagination beyond page 1 breaks.

## Files to Modify

| File | Action | Done | Purpose |
|------|--------|------|---------|
| `scripts/wati_supabase_cli.py` | Modify | yes | Add `to-file` subparser + `handle_to_file_contacts` + `handle_to_file_messages` handlers |
| `shared/wati_client.py` | Fix bug | yes | Line 59: change `?page=` to `&page=` in `get_messages` URL |

## Verification

1. `python scripts/wati_supabase_cli.py to-file contacts --output /tmp/contacts.json` → file written, valid JSON array, count printed
2. `python scripts/wati_supabase_cli.py to-file messages --phone 1234567890 --output /tmp/msgs.json` → file written with message list
3. `python scripts/wati_supabase_cli.py to-file messages --all --output-dir /tmp/msgs/` → directory created, one file per contact
4. `python scripts/wati_supabase_cli.py to-file --help` prints subcommand list

## Implementation Notes

- `--output` / `--output-dir` should default to sensible local paths; do not require them
- Write JSON with `json.dump(..., indent=2)` for readability
- `get_messages` returns `{phone: [messages]}` dict — flatten to just the list when writing per-contact files
- Both handlers should `require=True` on `--phone` XOR `--all` — use `argparse` mutually exclusive group
