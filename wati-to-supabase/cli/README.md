# Connector CLI

CLI-first admin surface for the standalone `Wati -> Supabase` connector.

## Command Name

Use the simple binary name:

- `wati-supabase`

Do not broaden or rebrand the command yet. The product is explicitly
`Wati -> Supabase`.

## Purpose

This CLI is the operator control plane for the connector.

It should cover:

- installation and setup
- connection testing
- contact sync
- message backfill
- drift checks
- daemon control
- logs and health inspection

## Current Scaffold

This repo now includes a real CLI scaffold:

- repo-root wrapper: `./wati-supabase`
- Python entry point: `scripts/wati_supabase_cli.py`

Currently implemented:

- `wati-supabase test-connection`
- `wati-supabase status --tenant-id ...`
- `wati-supabase watch status --tenant-id ...`

Other command groups are scaffolded in the parser but intentionally return
"not implemented yet" for now.

## Near-Term Posture

- CLI-first, not UI-first
- scriptable first
- optional watcher commands allowed
- plain-text output, no TUI dependency
- watcher UX should stay simple: polling + readable console output

## Suggested Command Surface

```bash
wati-supabase setup
wati-supabase test-connection
wati-supabase status --tenant 310131
wati-supabase sync contacts --tenant 310131
wati-supabase backfill messages --tenant 310131
wati-supabase drift check --tenant 310131
wati-supabase logs webhook --tenant 310131 --tail
wati-supabase doctor --tenant 310131
wati-supabase daemon run
wati-supabase watch status --tenant 310131
wati-supabase watch jobs --tenant 310131
```

## Explicit Non-Goals

- building a separate connector web UI in Phase A
- mixing classifier configuration into connector administration
- turning the connector into a generic messaging platform before the Wati
  product is clean
