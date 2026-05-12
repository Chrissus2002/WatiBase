# WatiBase

A CLI tool that syncs WhatsApp contact and message data from [Wati](https://www.wati.io/) into a [Supabase](https://supabase.com/) (PostgreSQL) database.

With it you can:

- Import all your Wati contacts into Supabase
- Backfill your full WhatsApp message history
- Receive new messages in real time via a webhook
- Export contacts or messages to local JSON files

## Prerequisites

- **Python 3.11+**
- A **Supabase** project (you'll need the URL and a service-role API key)
- A **Wati** account (you'll need the API URL and API token)

## Getting started

All commands are run from this root directory using the `./wati-supabase` wrapper.

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Initialize the database

Run the schema file against your Supabase database to create the required tables and functions. You can do this through the Supabase SQL Editor:

1. Open your Supabase project dashboard
2. Go to **SQL Editor**
3. Paste the contents of `database/init_db.sql` and run it
4. Paste the contents of `database/functions.sql` and run it

### 3. Configure credentials

Run the interactive setup wizard. It will ask for your Supabase URL, API key, Wati API URL, Wati token, and tenant details. Everything is saved into the database and a local `.env` file.

```bash
./wati-supabase setup
```

### 4. Verify the connection

```bash
./wati-supabase test-connection
```

### 5. Sync your data

```bash
# Import contacts from Wati
./wati-supabase sync contacts --tenant-id <ID>

# Backfill message history (runs in the background, resumable)
./wati-supabase backfill --tenant-id <ID>
```

The `--tenant-id` value is printed at the end of the `setup` step.

## Commands

### Sync and backfill

```bash
# Import contacts from Wati into Supabase
./wati-supabase sync contacts --tenant-id <ID>

# Import contacts but only fetch the first N pages (useful for testing)
./wati-supabase sync contacts --tenant-id <ID> --limit 2

# Backfill all message history (runs in the background)
./wati-supabase backfill --tenant-id <ID>

# Resume an interrupted backfill
./wati-supabase backfill --tenant-id <ID> --resume <JOB_ID>
```

### Monitoring

```bash
# Show connector status (credentials, tenant info)
./wati-supabase status --tenant-id <ID>

# Continuously poll status (refreshes every 10s, Ctrl+C to stop)
./wati-supabase watch status --tenant-id <ID>
./wati-supabase watch status --tenant-id <ID> --interval 30
```

### Exporting to files

```bash
# Download all contacts as JSON
./wati-supabase to-file contacts --tenant-id <ID> --output contacts.json

# Download messages for a single phone number
./wati-supabase to-file messages --tenant-id <ID> --phone 5215512345678

# Download messages for all contacts (one file per contact)
./wati-supabase to-file messages --tenant-id <ID> --all
```

### Logs

Background jobs (like `backfill`) write logs to the `logs/` directory.

```bash
# List available log files
./wati-supabase logs --list

# View a specific log
./wati-supabase logs --file backfill_1_20250101_120000.log

# Show only the last 50 lines
./wati-supabase logs --file backfill_1_20250101_120000.log --tail
```

## How it works

Data flows through a layered pipeline:

```
Wati API  -->  Staging (raw snapshots)  -->  Canonical (normalized)
```

1. **Staging** -- Raw data from Wati is stored as-is. Contacts are upserted (latest snapshot wins), messages are append-only.
2. **Canonical** -- A database function promotes staged data into clean, normalized records. Phone numbers are parsed into E.164 format and deduplicated.
3. **Ops** -- Background jobs are tracked in the `ops.import_jobs` table so backfills can resume from where they left off if interrupted.

### Real-time webhooks
A Supabase Edge Function at `supabase/functions/wati-webhook/` can receive live message events from Wati. Once deployed, new messages flow into the same staging-to-canonical pipeline automatically. See `supabase/functions/wati-webhook/index.ts` for the code, to deploy copy the content of the file to a new edge function in the Supabase dashboard.

For further details, see `supabase/functions/wati-webhook/README.md`.

## Project structure

```
.
├── scripts/              # CLI entry point
├── shared/               # Core Python modules (API client, importers, phone parsing)
├── database/             # SQL schema, functions, and migrations
│   ├── init_db.sql       # Full schema (run this on a fresh database)
│   ├── functions.sql     # SQL functions for data promotion
│   └── drop_db.sql       # Drop all tables and functions
├── supabase/functions/   # Supabase Edge Function for webhooks
├── tests/                # Unit tests
├── wati-supabase         # Shell wrapper (entry point)
├── requirements.txt      # Python dependencies
└── .env                  # Local credentials (created by setup, not committed)
```
