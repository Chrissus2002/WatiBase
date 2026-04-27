# SupaWati

A CLI tool that syncs WhatsApp contact and message data from the [Wati](https://www.wati.io/) API into a [Supabase](https://supabase.com/) database. Fetch contacts, backfill message history, or download everything to local JSON files.

## Prerequisites

- Python 3.11+
- A Supabase instance with credentials (URL + API key)
- Wati API credentials (account ID, API URL, token)

## Setup

```bash
pip install -r requirements.txt
./wati-supabase setup              # interactive credential wizard
./wati-supabase test-connection --tenant-id <ID>
```

## Commands

| Command | Description |
|---------|-------------|
| `setup` | Configure Supabase and Wati credentials for a tenant |
| `test-connection` | Verify Supabase connectivity |
| `status` | Show connector status for a tenant |
| `watch status` | Continuously poll and display status |
| `sync contacts` | Import contacts from Wati into Supabase |
| `backfill` | Fetch historical messages for all contacts (resumable) |
| `to-file contacts` | Download contacts as JSON |
| `to-file messages` | Download messages as JSON (single phone or all) |
| `logs` | Inspect, list, or tail background job logs |

## Usage

```bash
# Sync contacts from Wati into Supabase
./wati-supabase sync contacts --tenant-id 310131

# Backfill message history
./wati-supabase backfill --tenant-id 310131

# Export contacts to a file
./wati-supabase to-file contacts --tenant-id 310131 --output contacts.json
```

## How it works

Data flows through a layered pipeline:

1. **Wati API** -- raw contacts and messages are fetched in batches
2. **Staging** -- immutable snapshots stored in Supabase
3. **Canonical** -- normalized, deduplicated records with E.164 phone identities
4. **Analytics** -- downstream reporting projections

Background jobs (like `backfill`) are tracked and can resume from checkpoints if interrupted. The system is fully multi-tenant -- each tenant gets its own Wati connector config and isolated data.
